# hybrid_deduplicator.py
import threading
import os
from media_scanner import MediaScanner
from image_deduplicator import ImageDeduplicator
from video_deduplicator import VideoDeduplicator
from checkpoint import Checkpoint
from utils import get_file_size_mb, log_info, log_success


class HybridMediaDeduplicator:
    def __init__(self, folder_path, max_workers=4,
                 stop_event=None, progress_callback=None,
                 fail_callback=None, resume=True):
        self.folder_path = folder_path
        self.max_workers = max_workers
        self.stop_event = stop_event or threading.Event()
        self.progress_callback = progress_callback
        self.all_duplicates = []
        self.summary = {}

        # ── checkpoint ──
        self.checkpoint = Checkpoint() if resume else None
        self._resume = resume

        if self.checkpoint:
            matched = self.checkpoint.ensure_folder_match(folder_path)
            if not matched:
                log_info("文件夹已变更，清空旧 checkpoint")
            status = self.checkpoint.get_status()
            counts = status.get("_counts", {})
            if counts.get("images", 0) > 0 or counts.get("videos", 0) > 0:
                log_info(f"发现断点: {counts.get('images', 0)} 图 + "
                         f"{counts.get('videos', 0)} 视频已处理, "
                         f"{counts.get('duplicates', 0)} 组重复已记录")

        self.scanner = MediaScanner(folder_path,
                                    stop_event=self.stop_event,
                                    progress_callback=self.progress_callback)

        # 实时发现重复的回调
        def _found_cb(source, count):
            if self.progress_callback:
                self.progress_callback("found", count, 0)

        self.image_dedup = ImageDeduplicator(max_workers,
                                              stop_event=self.stop_event,
                                              progress_callback=self.progress_callback,
                                              found_callback=_found_cb)
        if self.checkpoint:
            self.image_dedup.set_checkpoint(self.checkpoint)

        self.video_dedup = VideoDeduplicator(max_workers,
                                              stop_event=self.stop_event,
                                              progress_callback=self.progress_callback,
                                              found_callback=_found_cb)
        if self.checkpoint:
            self.video_dedup.set_checkpoint(self.checkpoint)

        if fail_callback:
            self.image_dedup.set_fail_callback(fail_callback)
            self.video_dedup.set_fail_callback(fail_callback)

    def _check_stop(self):
        return self.stop_event.is_set()

    def run(self):
        log_info("=" * 50)
        log_info("多媒体去重 — checkpoint + 锚点评分")
        log_info("=" * 50)

        cp = self.checkpoint

        # ── 阶段1: 扫描（始终执行，毫秒级）───
        if self._check_stop():
            return False
        cnt = self.scanner.scan()
        if cnt == 0:
            return False
        total_images = len(self.scanner.image_files)
        total_videos = len(self.scanner.video_files)

        # ── 阶段2: 图片 ──
        if cp and cp.is_phase_done("images") and cp.is_phase_done("images_full"):
            log_info("阶段2 图片处理已完成，从 checkpoint 恢复")
            for path, phash_str, _size in cp.get_processed_images():
                self.image_dedup.file_to_hash[path] = phash_str
                self.image_dedup.image_hashes[phash_str].append(path)
            self.image_dedup.find_duplicates()
            if self.image_dedup.duplicate_groups:
                self.all_duplicates.extend(self.image_dedup.duplicate_groups)
        elif total_images > 0:
            if self._check_stop():
                return False
            self.all_duplicates.extend(
                self.image_dedup.run(self.scanner.image_files)
            )
            if self._check_stop():
                return False  # 用户停止 → 保留 checkpoint 供续跑
            if cp:
                cp.save_progress("images_full", total_images, total_images)
        if self._check_stop():
            return False

        # ── 阶段3: 视频提取 ──
        if cp and cp.is_phase_done("videos"):
            log_info("阶段3 视频提取已完成，从 checkpoint 恢复")
            loaded = self.video_dedup.load_checkpoint()
            log_info(f"恢复 {loaded} 个视频的关键帧")
        elif total_videos > 0:
            if self._check_stop():
                return False
            self.video_dedup.process_videos(self.scanner.video_files)
            if self._check_stop():
                return False
        if self._check_stop():
            return False

        # ── 阶段4: 视频匹配 ──
        if cp and cp.is_phase_done("matching"):
            log_info("阶段4 视频匹配已完成，从 checkpoint 读取结果")
            self.all_duplicates.extend(cp.get_duplicates())
            self._sort_groups()
        elif len(self.video_dedup.video_keyframe_hashes) >= 2:
            if self._check_stop():
                return False
            self.video_dedup.find_identical_videos()
            self.video_dedup.find_edited_reencoded_videos()
            self.video_dedup._merge_connected_groups()
            self.all_duplicates.extend(self.video_dedup.duplicate_groups)
            if self._check_stop():
                return False
            if cp:
                cp.clear_duplicates()
                for idx, g in enumerate(self.all_duplicates, 1):
                    cp.save_duplicate_group(idx, g)
                cp.save_progress("matching", 1, 1)

        # ── 排序 ──
        self._sort_groups()

        # ── 阶段5: 报告 ──
        if cp:
            cp.save_progress("report", 1, 1)
            cp.cleanup()

        log_success("处理完成")
        return True

    def gather_partial_results(self):
        """暂停时收集已处理数据中的重复组（不排序、不清理 checkpoint）"""
        # 图片：用已算完的哈希直接找重复
        if self.image_dedup.file_to_hash:
            self.image_dedup.duplicate_groups.clear()
            self.image_dedup.find_duplicates()
            self.all_duplicates.extend(self.image_dedup.duplicate_groups)

        # 视频：如果匹配阶段已完成，加载结果
        cp = self.checkpoint
        if cp and cp.is_phase_done("matching"):
            self.all_duplicates.extend(cp.get_duplicates())

        # 暂停时不排序（避免 os.path.getsize 遍历全部文件卡住）

    def _sort_groups(self):
        """排序：先视频后图片，同类按组内文件总大小从大到小"""
        def sort_key(g):
            is_video = 0 if "video" in g.get("type", "") else 1
            total_size = sum(os.path.getsize(f) for f in g.get("files", [])
                             if os.path.exists(f))
            return (is_video, -total_size)
        self.all_duplicates.sort(key=sort_key)

    def get_summary(self):
        s = self.scanner.get_summary()
        total_dup = sum(g.get("count", 0) for g in self.all_duplicates)
        save = self._calc_save()
        self.summary = {
            "scan_folder": self.folder_path,
            "total_files": s["total_files"],
            "image_files": s["image_files"],
            "video_files": s["video_files"],
            "duplicate_groups": len(self.all_duplicates),
            "total_duplicates": total_dup,
            "can_save_mb": round(save, 2),
        }
        return self.summary

    def _calc_save(self):
        t = 0
        for g in self.all_duplicates:
            fs = g.get("files", [])
            if len(fs) < 2:
                continue
            sz = sorted([get_file_size_mb(f) for f in fs])
            t += sum(sz[1:])
        return t
