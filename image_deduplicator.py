# image_deduplicator.py
import threading
import os
import imagehash
from PIL import Image
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from config import IMAGE_HASH_ALGORITHM, IMAGE_HASH_SIZE
from utils import hamming_distance, log_warning, log_info

# ─── 模块级函数（ProcessPoolExecutor pickle 要求）───

def _hash_single_image(image_path):
    """计算单张图片的 pHash（模块级，可 pickle）"""
    try:
        img = Image.open(image_path)
        # 统一转 RGB：调色板→RGBA→RGB，透明通道→白底
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if IMAGE_HASH_ALGORITHM == "phash":
            h = imagehash.phash(img, hash_size=IMAGE_HASH_SIZE)
        elif IMAGE_HASH_ALGORITHM == "dhash":
            h = imagehash.dhash(img, hash_size=IMAGE_HASH_SIZE)
        else:
            h = imagehash.average_hash(img, hash_size=IMAGE_HASH_SIZE)
        return image_path, str(h), None
    except Exception as e:
        return image_path, None, str(e)


class ImageDeduplicator:
    def __init__(self, max_workers=4, stop_event=None, progress_callback=None,
                 found_callback=None):
        self.max_workers = max_workers
        self.image_hashes = defaultdict(list)
        self.file_to_hash = {}
        self.duplicate_groups = []
        self.fail_callback = None
        self.stop_event = stop_event or threading.Event()
        self.progress_callback = progress_callback
        self.found_callback = found_callback
        self._checkpoint = None
        self._last_reported_count = 0

    def set_fail_callback(self, cb):
        self.fail_callback = cb

    def set_checkpoint(self, cp):
        """注入 checkpoint 实例，用于批量写入和进度保存"""
        self._checkpoint = cp

    def process_images(self, files):
        if not files:
            return

        # ── 第1步：按文件大小分组，只处理大小冲突的 ──
        size_groups = defaultdict(list)
        for f in files:
            try:
                size_groups[os.path.getsize(f)].append(f)
            except OSError:
                size_groups[-1].append(f)  # 无法获取大小 → 单独处理

        to_process = []
        skipped = 0
        for size, file_list in size_groups.items():
            if len(file_list) >= 2:
                to_process.extend(file_list)
            else:
                skipped += 1

        log_info(
            f"大小分组: {len(files)} 张 → {len(to_process)} 张需算哈希 "
            f"({skipped} 种大小唯一，跳过)"
        )

        # ── 第2步：过滤已 checkpoint 的 ──
        if self._checkpoint:
            done = self._checkpoint.get_processed_image_paths()
            pending = [f for f in to_process if f not in done]
            log_info(f"Checkpoint: {len(done)} 已处理, {len(pending)} 待处理")
        else:
            pending = to_process

        if not pending:
            # 全从 checkpoint 恢复
            if self._checkpoint:
                for path, phash_str, _size in self._checkpoint.get_processed_images():
                    self.file_to_hash[path] = phash_str
                    self.image_hashes[phash_str].append(path)
            return

        total = len(pending)
        completed = 0
        batch = []
        batch_size = 500

        # ── 第3步：ProcessPoolExecutor 并行算哈希 ──
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_path = {
                executor.submit(_hash_single_image, f): f
                for f in pending
            }
            for future in as_completed(future_to_path):
                if self.stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    # 已完成的批次写入 checkpoint
                    if batch and self._checkpoint:
                        self._checkpoint.save_image_hashes_batch(batch)
                    return

                path = future_to_path[future]
                try:
                    path_out, h_str, error = future.result()
                except Exception as e:
                    log_warning(f"图片处理异常 {path}: {e}")
                    if self.fail_callback:
                        self.fail_callback(path, str(e))
                    continue

                if error:
                    log_warning(f"图片处理失败 {path_out}: {error}")
                    if self.fail_callback:
                        self.fail_callback(path_out, error)
                    continue

                if h_str:
                    self.file_to_hash[path_out] = h_str
                    self.image_hashes[h_str].append(path_out)

                    # 记入批量写入缓冲
                    if self._checkpoint:
                        try:
                            batch.append((path_out, h_str, os.path.getsize(path_out)))
                        except OSError:
                            batch.append((path_out, h_str, 0))

                completed += 1

                # 每 batch_size 张 commit 一次
                if len(batch) >= batch_size and self._checkpoint:
                    self._checkpoint.save_image_hashes_batch(batch)
                    self._checkpoint.save_progress("images", completed, total)
                    batch.clear()

                # 每 200 张检查新重复组
                if self.found_callback and completed % 200 == 0:
                    new_count = sum(1 for fs in self.image_hashes.values()
                                    if len(fs) > 1)
                    if new_count > self._last_reported_count:
                        self._last_reported_count = new_count
                        self.found_callback("images", new_count)

                if self.progress_callback and completed % 20 == 0:
                    self.progress_callback("images", completed, total)

        # 最后一批写入
        if batch and self._checkpoint:
            self._checkpoint.save_image_hashes_batch(batch)
            self._checkpoint.save_progress("images", total, total)

        if self.progress_callback:
            self.progress_callback("images", total, total)

        log_info(f"图片处理完成: {len(self.file_to_hash)} 张有效哈希")

    def find_duplicates(self):
        """从哈希表中找出重复组"""
        for h_str, fs in self.image_hashes.items():
            if len(fs) > 1:
                self.duplicate_groups.append({
                    "type": "image",
                    "files": fs,
                    "count": len(fs),
                    "similarity": "identical",
                    "reason": "内容完全相同（指纹一致）",
                })

    def run(self, files):
        self.process_images(files)
        self.find_duplicates()
        return self.duplicate_groups
