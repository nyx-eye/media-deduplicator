"""
视频去重模块 v2.0
- GPU 加速场景检测（ffmpeg NVDEC / CuPy / CPU 三级回退）
- 倒排索引 + 三层漏斗快速匹配（替代 O(n²) 两两比对）
- 滑动窗口序列对齐（替代位置锁定比对）
- 两级场景切换检测（absdiff → SSIM）
- 帧跳策略 + ProcessPoolExecutor
"""

import os
import cv2
import imagehash
import hashlib
import numpy as np
from PIL import Image
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

from config import (
    VIDEO_FRAME_RESIZE, VIDEO_FRAME_SKIP, VIDEO_MAX_FRAMES,
    VIDEO_SIMILARITY_THRESHOLD, VIDEO_HASH_THRESHOLD,
    HIST_BINS, HIST_RANGE, HIST_THRESHOLD,
    SSIM_THRESHOLD, ABSDIFF_THRESHOLD,
    MIN_SHARED_FRAMES, DURATION_RATIO_THRESHOLD,
    ENABLE_DURATION_PREFILTER,
    USE_GPU_DECODE, SAVE_KEYFRAMES
)
from utils import (
    hamming_distance, get_video_metadata,
    log_warning, log_info, log_success
)


# ═══════════════════════════════════════════════════════════════
# GPU 加速工具检测
# ═══════════════════════════════════════════════════════════════

def _check_ffmpeg_cuda():
    """检测 ffmpeg 是否支持 NVDEC CUDA 硬件加速"""
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-hwaccels"], capture_output=True, text=True, timeout=5
        )
        return "cuda" in result.stdout.lower()
    except Exception:
        return False


# 模块级缓存检测结果
_FFMPEG_CUDA_AVAILABLE = None


def _get_gpu_engine():
    """返回可用的 GPU 加速引擎：'ffmpeg' 或 None"""
    global _FFMPEG_CUDA_AVAILABLE
    if USE_GPU_DECODE:
        if _FFMPEG_CUDA_AVAILABLE is None:
            _FFMPEG_CUDA_AVAILABLE = _check_ffmpeg_cuda()
        if _FFMPEG_CUDA_AVAILABLE:
            return "ffmpeg"
    return None


# ═══════════════════════════════════════════════════════════════
# 独立处理函数（供 ProcessPoolExecutor 使用，必须 top-level）
# ═══════════════════════════════════════════════════════════════

def _process_single_video(args):
    """单个视频的关键帧提取（独立函数，pickle 安全）"""
    video_path, gpu_engine = args
    try:
        # GPU 引擎检测已在主进程完成，这里直接调用对应路径
        # 三级回退：NVDEC → ffmpeg软件 → OpenCV
        if gpu_engine == "ffmpeg":
            hashes = _extract_with_ffmpeg(video_path)
            if hashes is None:
                hashes = _extract_with_cpu(video_path)
        else:
            hashes = _extract_with_cpu(video_path)

        meta = get_video_metadata(video_path)
        return video_path, hashes, meta
    except Exception as e:
        log_warning(f"视频处理失败 {video_path}: {str(e)}")
        return video_path, None, get_video_metadata(video_path)


# ═══════════════════════════════════════════════════════════════
# 关键帧提取：三种引擎
# ═══════════════════════════════════════════════════════════════

def _compute_frame_hash(frame):
    """单帧感知哈希（提取到外层，供三种引擎复用）"""
    try:
        small = cv2.resize(frame, VIDEO_FRAME_RESIZE)
        img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)).convert('L')
        return imagehash.phash(img, hash_size=8)
    except Exception:
        return None


def _is_scene_change_fast(frame, prev_keyframe):
    """两级场景切换检测：absdiff（快速）→ SSIM（仅在灰色地带）

    第1级 absdiff 过滤 90%+ 的帧，只有灰色地带才用 SSIM
    """
    if prev_keyframe is None:
        return True

    try:
        gray_cur = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_prev = cv2.cvtColor(prev_keyframe, cv2.COLOR_BGR2GRAY)

        # ---- 第1级：absdiff 快速过滤 ----
        diff = cv2.absdiff(gray_cur, gray_prev)
        mean_diff = diff.mean()

        if mean_diff < ABSDIFF_THRESHOLD * 0.5:
            return False  # 几乎相同 → 不是切换
        if mean_diff > ABSDIFF_THRESHOLD * 2.0:
            return True   # 明显不同 → 是切换

        # ---- 第2级：灰色地带回退到 SSIM ----
        from skimage.metrics import structural_similarity as ssim
        score, _ = ssim(gray_cur, gray_prev, full=True)
        if score < SSIM_THRESHOLD:
            return True

        # ---- 直方图辅助 ----
        hsv_cur = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hsv_prev = cv2.cvtColor(prev_keyframe, cv2.COLOR_BGR2HSV)
        h_bins, s_bins = HIST_BINS
        hist_cur = cv2.calcHist([hsv_cur], [0, 1], None, [h_bins, s_bins], HIST_RANGE)
        hist_prev = cv2.calcHist([hsv_prev], [0, 1], None, [h_bins, s_bins], HIST_RANGE)
        cv2.normalize(hist_cur, hist_cur)
        cv2.normalize(hist_prev, hist_prev)
        try:
            dist = cv2.compareHist(
                hist_cur.astype('float32'), hist_prev.astype('float32'),
                cv2.HISTCMP_BHATTACHARYYA
            )
        except Exception:
            dist = float(np.linalg.norm(hist_cur - hist_prev))
        return dist > HIST_THRESHOLD

    except Exception:
        return True


def _extract_with_cpu(video_path):
    """CPU 引擎：帧跳 + 两级场景检测"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        return None

    keyframe_hashes = []
    prev_keyframe = None
    frame_idx = 0
    keyframe_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        # 帧跳策略：只处理每第 N 帧
        if frame_idx % VIDEO_FRAME_SKIP != 0:
            continue

        # 场景切换检测
        if _is_scene_change_fast(frame, prev_keyframe):
            h = _compute_frame_hash(frame)
            if h:
                keyframe_hashes.append(h)
                prev_keyframe = frame.copy()
                keyframe_idx += 1

        if keyframe_idx >= VIDEO_MAX_FRAMES:
            break

    cap.release()
    return keyframe_hashes if keyframe_hashes else None


def _detect_scenes_unified(frames):
    """统一场景检测：与 CPU 路径 _is_scene_change_fast 逻辑一致

    保证 GPU/CPU 不同路径产出的关键帧可直接比对，避免混合路径时漏检。

    Args:
        frames: BGR numpy 数组列表（已缩放至 VIDEO_FRAME_RESIZE）
    Returns:
        list of imagehash, or None
    """
    if not frames:
        return None

    keyframe_hashes = []
    prev_keyframe = None  # BGR 原图，供 _is_scene_change_fast 使用

    for frame in frames:
        if _is_scene_change_fast(frame, prev_keyframe):
            h = _compute_frame_hash(frame)
            if h:
                keyframe_hashes.append(h)
                prev_keyframe = frame.copy()

        if len(keyframe_hashes) >= VIDEO_MAX_FRAMES:
            break

    return keyframe_hashes if keyframe_hashes else None


def _extract_with_ffmpeg(video_path):
    """GPU 引擎：NVDEC 解码 + 轻量跳帧/缩放 → rawvideo tempfile

    管线：
      NVDEC 解码(GPU) → hwdownload(自动) → fps跳帧 + scale + format
        → rawvideo tempfile → numpy 直接构造帧 → absdiff 场景检测 → pHash

    核心加速靠 NVDEC 硬件解码（占总耗时 80%+）。
    rawvideo 用 tempfile 而非 pipe（Windows pipe 不稳定）。
    """
    import subprocess
    import tempfile
    import numpy as np

    w, h = VIDEO_FRAME_RESIZE
    skip = VIDEO_FRAME_SKIP   # 统一采样密度，保证 GPU/CPU 路径关键帧可比
    max_out = min(VIDEO_MAX_FRAMES * 3, 3000)
    frame_bytes = w * h * 3

    # 按帧序号跳帧，不依赖时间戳（兼容 VFR/损坏时间戳）
    q = chr(39)
    select_expr = f"select={q}not(mod(n,{skip})){q}"

    # NVDEC 路径：hwdownload → select → scale → format
    vf_nvdec = f"hwdownload,format=nv12,{select_expr},scale={w}:{h},format=bgr24"

    # ── 主路径: NVDEC 硬件解码 ──
    tmpfile = None
    try:
        fd, tmpfile = tempfile.mkstemp(suffix=".raw", prefix="vdedup_")
        os.close(fd)

        result = subprocess.run([
            "ffmpeg", "-y",
            "-fflags", "+genpts+igndts",
            "-err_detect", "ignore_err",
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-i", video_path,
            "-vf", vf_nvdec,
            "-vsync", "0",
            "-frames:v", str(max_out),
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            tmpfile
        ], capture_output=True, timeout=300)

        # NVDEC 失败 → 直接回退 OpenCV（不再试 Gyan 软件解码）
        # 原因：NVDEC 和 Gyan ffmpeg 共用一个容器解析器，前者打不开后者也打不开
        if result.returncode != 0:
            return None

        # 读取 raw 数据 → numpy 帧序列
        raw = np.fromfile(tmpfile, dtype=np.uint8)
        if len(raw) < frame_bytes * 2:
            return None

        num_frames = len(raw) // frame_bytes
        frames = []
        for i in range(num_frames):
            offset = i * frame_bytes
            chunk = raw[offset:offset + frame_bytes]
            if len(chunk) < frame_bytes:
                break
            frames.append(chunk.reshape(h, w, 3))

        if len(frames) < 2:
            return None

        return _detect_scenes_unified(frames)

    except subprocess.TimeoutExpired:
        log_warning(f"ffmpeg 超时: {video_path}，回退 CPU")
        return _extract_with_cpu(video_path)
    except Exception as e:
        log_warning(f"ffmpeg 失败: {video_path} ({e})，回退 CPU")
        return _extract_with_cpu(video_path)
    finally:
        if tmpfile and os.path.exists(tmpfile):
            os.unlink(tmpfile)


# ═══════════════════════════════════════════════════════════════
# 倒排索引快速匹配引擎
# ═══════════════════════════════════════════════════════════════

class FastVideoMatcher:
    """基于倒排索引的三层漏斗匹配器

    第1层：时长预过滤（O(1) 查找）
    第2层：倒排索引 共享关键帧计数（O(K × B)，K=总关键帧数，B=平均桶大小）
    第3层：滑动窗口序列精排（O(c × len1 × len2)，c=候选对数，通常 < 500）
    """

    def __init__(self, hash_threshold=VIDEO_HASH_THRESHOLD,
                 min_shared=MIN_SHARED_FRAMES,
                 sim_threshold=VIDEO_SIMILARITY_THRESHOLD,
                 duration_ratio=DURATION_RATIO_THRESHOLD):
        self.hash_threshold = hash_threshold
        self.min_shared = min_shared
        self.sim_threshold = sim_threshold
        self.duration_ratio = duration_ratio

        # 倒排索引: hash_int → [(video_path, frame_pos), ...]
        self.inverted_index = defaultdict(list)

        # 视频 → 关键帧哈希列表
        self.video_hashes = {}

        # 视频 → 元数据
        self.video_meta = {}

    # ── 索引构建 ──

    def build_index(self, video_keyframe_hashes, video_metadata=None):
        """构建倒排索引（记录帧位置），O(K)"""
        self.inverted_index.clear()
        self.video_hashes = video_keyframe_hashes
        self.video_meta = video_metadata or {}

        for video_path, hashes in video_keyframe_hashes.items():
            if not hashes:
                continue
            for frame_pos, phash in enumerate(hashes):
                hash_int = int(str(phash), 16)
                self.inverted_index[hash_int].append((video_path, frame_pos))

        log_info(f"倒排索引: {len(self.inverted_index)} 唯一哈希, "
                 f"{len(video_keyframe_hashes)} 视频")

    # ── 时长预过滤 ──

    def _duration_filter(self, v1, v2):
        if not ENABLE_DURATION_PREFILTER:
            return True
        m1, m2 = self.video_meta.get(v1), self.video_meta.get(v2)
        if not m1 or not m2:
            return True
        d1, d2 = m1.get("duration", 0), m2.get("duration", 0)
        if d1 <= 0 or d2 <= 0:
            return True
        ratio = min(d1, d2) / max(d1, d2) if max(d1, d2) > 0 else 1.0
        return ratio >= self.duration_ratio

    @staticmethod
    def _reason(score):
        if score >= 95:
            return "高度相似（可能是副本）"
        elif score >= 85:
            return "压缩/重编码/不同分辨率"
        elif score >= 60:
            return "轻度剪辑"
        return "剪辑+压缩"

    # ── 锚点评分（一步到位的候选发现+打分）───

    def find_and_score_all(self):
        """倒排索引 + 锚点评分，一步到位

        倒排索引记录了帧位置 → 直接提取共享帧对 (pos1, pos2)
        → 检查 v1/v2 位置序列是否单调 → 单调则直接出分
        → 不单调的存疑对走滑动窗口
        """
        # 1. 收集所有共享帧对
        pair_frames = defaultdict(list)  # (v1,v2) → [(pos1,pos2), ...]

        for hash_int, entries in self.inverted_index.items():
            if len(entries) < 2:
                continue
            # 找出共享该哈希的所有视频对
            for i in range(len(entries)):
                v1, p1 = entries[i]
                for j in range(i + 1, len(entries)):
                    v2, p2 = entries[j]
                    if v1 == v2:
                        continue
                    key = (v1, v2) if v1 < v2 else (v2, v1)
                    pair_frames[key].append((p1, p2))

        # 2. 锚点评分
        results = []
        slide_candidates = []

        for (v1, v2), pairs in pair_frames.items():
            if len(pairs) < self.min_shared:
                continue
            if not self._duration_filter(v1, v2):
                continue

            len1 = len(self.video_hashes.get(v1, []))
            len2 = len(self.video_hashes.get(v2, []))
            if len1 == 0 or len2 == 0:
                continue

            score = self._anchor_score(pairs, len1, len2)

            if score is not None and score >= self.sim_threshold:
                results.append((v1, v2, score, self._reason(score)))
            elif score is not None and score < self.sim_threshold:
                # 锚点分不够但共享帧足够 → 存疑，走滑动窗口
                slide_candidates.append((v1, v2, len(pairs)))
            elif score is None:
                # 顺序混乱 → 走滑动窗口
                slide_candidates.append((v1, v2, len(pairs)))

        log_info(
            f"锚点评分: {len(results)} 对直接出分, "
            f"{len(slide_candidates)} 对需滑动窗口 "
            f"(共 {len(self.video_hashes) * max(0, len(self.video_hashes) - 1) // 2} 可能对)"
        )

        # 3. 存疑对 → 滑动窗口精排
        for v1, v2, _shared in slide_candidates:
            sw_score = self.sliding_window_similarity(
                self.video_hashes[v1], self.video_hashes[v2]
            )
            if sw_score >= self.sim_threshold:
                results.append((v1, v2, sw_score, self._reason(sw_score)))

        results.sort(key=lambda x: -x[2])
        log_info(f"匹配完成: {len(results)} 组重复视频")
        return results

    def _anchor_score(self, pairs, len1, len2):
        """锚点评分：共享帧顺序一致 → 直接出分；顺序乱 → None

        pairs: [(pos_in_v1, pos_in_v2), ...]
        """
        pairs.sort(key=lambda x: x[0])
        v2_seq = [p[1] for p in pairs]

        inversions = sum(
            1 for i in range(len(v2_seq) - 1) if v2_seq[i] > v2_seq[i + 1]
        )

        if inversions <= max(2, len(pairs) * 0.1):  # 基本有序
            return len(pairs) / min(len1, len2) * 100
        return None

    # ── 滑动窗口（仅存疑对使用）───

    def sliding_window_similarity(self, h1_list, h2_list):
        """滑动窗口序列相似度"""
        if not h1_list or not h2_list:
            return 0.0

        shorter, longer = (h1_list, h2_list) \
            if len(h1_list) <= len(h2_list) else (h2_list, h1_list)

        best_match = 0
        max_offset = len(longer) - len(shorter) + 1
        if max_offset <= 0:
            max_offset = 1

        shorter_ints = [int(str(h), 16) for h in shorter]
        longer_ints = [int(str(h), 16) for h in longer]

        for offset in range(max_offset):
            match_count = 0
            for i, h_s in enumerate(shorter_ints):
                if (h_s ^ longer_ints[offset + i]).bit_count() <= self.hash_threshold:
                    match_count += 1
            if match_count > best_match:
                best_match = match_count
                if best_match == len(shorter):
                    break

        return (best_match / len(shorter)) * 100


# ═══════════════════════════════════════════════════════════════
# VideoDeduplicator 主类
# ═══════════════════════════════════════════════════════════════

class VideoDeduplicator:
    """视频去重器 v2.0

    改进点：
    - GPU 加速（ffmpeg NVDEC → CuPy → CPU 三级回退）
    - 倒排索引快速匹配（O(n) 替代 O(n²)）
    - 滑动窗口序列对齐
    - 两级场景切换检测
    - ProcessPoolExecutor 并行处理
    - 模糊"完全相同"判定
    """

    def __init__(self, max_workers=4, stop_event=None, progress_callback=None,
                 found_callback=None):
        self.max_workers = max_workers
        self.video_keyframe_hashes = {}
        self.video_metadata = {}
        self.duplicate_groups = []
        self.fail_callback = None
        self.stop_event = stop_event
        self.progress_callback = progress_callback
        self.found_callback = found_callback
        self._executor = None
        self._checkpoint = None

        self.gpu_engine = _get_gpu_engine()
        if self.gpu_engine:
            log_success(f"GPU 加速引擎: {self.gpu_engine} (RTX 4060)")
        else:
            log_info("GPU 加速未启用，使用 CPU 模式")

    def set_fail_callback(self, cb):
        self.fail_callback = cb

    def set_checkpoint(self, cp):
        self._checkpoint = cp

    def set_fail_callback(self, cb):
        self.fail_callback = cb

    # ═══ 关键帧提取 ═══

    def extract_keyframes(self, video_path):
        """单视频关键帧提取（供 ProcessPoolExecutor 调用）"""
        return _process_single_video((video_path, self.gpu_engine))

    # ═══ 视频处理 ═══

    def process_videos(self, video_files):
        """并行处理所有视频，提取关键帧序列"""
        if not video_files:
            return

        # 过滤已 checkpoint 的视频（断点续跑）
        if self._checkpoint:
            done = self._checkpoint.get_processed_video_paths()
            pending = [v for v in video_files if v not in done]
            if len(pending) < len(video_files):
                log_info(f"Checkpoint: {len(done)} 视频已处理, {len(pending)} 待处理")
        else:
            pending = video_files

        if not pending:
            return

        gpu_label = f"GPU({self.gpu_engine})" if self.gpu_engine else "CPU"
        log_info(f"正在提取关键帧 [{gpu_label}]: {len(pending)} 个视频")

        # 恢复已 checkpoint 的数据
        already_done = len(video_files) - len(pending)
        if already_done > 0:
            self.load_checkpoint()

        tasks = [(path, self.gpu_engine) for path in pending]
        completed = 0
        total = len(pending)
        global_total = len(video_files)

        self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        try:
            future_to_path = {
                self._executor.submit(_process_single_video, task): task[0]
                for task in tasks
            }
            for future in tqdm(as_completed(future_to_path),
                               total=len(future_to_path), desc="关键帧提取"):
                # 检查停止
                if self.stop_event and self.stop_event.is_set():
                    log_info("视频处理被用户停止，取消剩余任务...")
                    self._executor.shutdown(wait=False, cancel_futures=True)
                    self._executor = None
                    return

                path = future_to_path[future]
                try:
                    _, h_list, meta = future.result(timeout=1)
                except Exception as e:
                    log_warning(f"处理异常 {path}: {e}")
                    if self.fail_callback:
                        self.fail_callback(path, str(e))
                    continue

                if h_list:
                    self.video_keyframe_hashes[path] = h_list
                    # 每处理完 10 个视频就写入 checkpoint
                    if self._checkpoint and completed % 10 == 0:
                        self._checkpoint.save_video_keyframes(path, h_list, meta)
                else:
                    log_warning(f"未能提取关键帧: {path}")
                    if self.fail_callback:
                        self.fail_callback(path, "未能提取有效关键帧")

                if meta:
                    self.video_metadata[path] = meta

                completed += 1
                if self.progress_callback:
                    self.progress_callback("videos_extract",
                                           already_done + completed, global_total)
                if self._checkpoint:
                    self._checkpoint.save_progress("videos",
                                                   already_done + completed, global_total)

        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
                self._executor = None

        log_info(f"关键帧提取完成: {len(self.video_keyframe_hashes)}/{global_total} 成功")

    # ═══ checkpoint 恢复 ═══

    def load_checkpoint(self):
        """从 checkpoint 恢复已处理的视频数据"""
        if not self._checkpoint:
            return 0
        data = self._checkpoint.get_processed_videos()
        for path, info in data.items():
            hashes = info["hashes"]
            if hashes:
                # 字符串哈希转回 imagehash 对象
                import imagehash
                restored = []
                for h_str in hashes:
                    try:
                        restored.append(imagehash.hex_to_hash(h_str))
                    except Exception:
                        pass
                if restored:
                    self.video_keyframe_hashes[path] = restored
            if info["meta"]:
                self.video_metadata[path] = info["meta"]
        return len(data)

    # ═══ 完全相同判定（模糊版） ═══

    def find_identical_videos(self):
        """模糊匹配判定完全相同（允许 ≤2 帧差异）

        不再使用脆弱的 MD5 串联，改用关键帧序列海明距离比较。
        """
        videos = list(self.video_keyframe_hashes.keys())
        if len(videos) < 2:
            return

        # 用倒排索引加速：哈希完全相同的帧计数
        hash_map = defaultdict(list)  # frame_hash → [video_path]
        for path, hashes in self.video_keyframe_hashes.items():
            for h in hashes:
                hash_map[int(str(h), 16)].append(path)

        # 统计视频对共享多少完全相同的关键帧
        pair_exact = defaultdict(int)
        for h_val, vlist in hash_map.items():
            if len(vlist) < 2:
                continue
            unique_v = list(set(vlist))
            for i in range(len(unique_v)):
                for j in range(i + 1, len(unique_v)):
                    key = tuple(sorted([unique_v[i], unique_v[j]]))
                    pair_exact[key] += 1

        for (v1, v2), exact_count in pair_exact.items():
            h1 = self.video_keyframe_hashes[v1]
            h2 = self.video_keyframe_hashes[v2]
            min_len = min(len(h1), len(h2))

            # 完全相同判定：>= 95% 的关键帧完全一致，且最多 2 帧差异
            if exact_count >= min_len - 2 and exact_count >= min_len * 0.95:
                self.duplicate_groups.append({
                    "type": "video",
                    "files": [v1, v2],
                    "count": 2,
                    "similarity": f"{exact_count / min_len * 100:.1f}%",
                    "reason": "关键帧序列高度一致（完全相同）"
                })

    # ═══ 编辑/重编码判定（倒排索引） ═══

    def find_edited_reencoded_videos(self):
        """倒排索引 + 锚点评分，一步到位"""
        if len(self.video_keyframe_hashes) < 2:
            return

        if self.progress_callback:
            self.progress_callback("videos_match", 0, 1)

        matcher = FastVideoMatcher(
            hash_threshold=VIDEO_HASH_THRESHOLD,
            min_shared=MIN_SHARED_FRAMES,
            sim_threshold=VIDEO_SIMILARITY_THRESHOLD
        )
        matcher.build_index(self.video_keyframe_hashes, self.video_metadata)
        results = matcher.find_and_score_all()

        if self.progress_callback:
            self.progress_callback("videos_match", 1, 1)

        existing_pairs = set()
        for g in self.duplicate_groups:
            if g["type"] == "video":
                existing_pairs.add(tuple(sorted(g["files"])))

        for v1, v2, score, reason in results:
            pair = tuple(sorted([v1, v2]))
            if pair in existing_pairs:
                continue
            group = {
                "type": "video_edited",
                "files": [v1, v2],
                "count": 2,
                "similarity": f"{score:.1f}%",
                "reason": reason,
            }
            self.duplicate_groups.append(group)

            # 及时写入 checkpoint
            if self._checkpoint:
                gid = len(self.duplicate_groups)
                self._checkpoint.save_duplicate_group(gid, group)

        # 视频匹配完成，回调通知 GUI
        if self.found_callback:
            video_groups = sum(1 for g in self.duplicate_groups
                               if g["type"] in ("video", "video_edited"))
            if video_groups > 0:
                self.found_callback("videos", video_groups)

    # ═══ 主流程 ═══

    def _merge_connected_groups(self):
        """并查集合并：把 (A,B), (B,C) 合并为 [A,B,C] 单个组"""
        parent = {}

        def find(x):
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(a, b):
            parent[find(a)] = find(b)

        for g in self.duplicate_groups:
            fs = g["files"]
            for i in range(len(fs) - 1):
                union(fs[i], fs[i + 1])

        # 按根节点收集
        merged = defaultdict(list)
        for g in self.duplicate_groups:
            root = find(g["files"][0])
            merged[root].append(g)

        new_groups = []
        for root, groups in merged.items():
            all_files = []
            seen = set()
            best_score = 0.0
            best_reason = ""
            gtype = groups[0]["type"]
            for g in groups:
                for f in g["files"]:
                    if f not in seen:
                        all_files.append(f)
                        seen.add(f)
                s = float(g["similarity"].rstrip("%"))
                if s > best_score:
                    best_score = s
                    best_reason = g["reason"]
            new_groups.append({
                "type": gtype if len(groups) > 1 or gtype == "video_edited" else gtype,
                "files": all_files,
                "count": len(all_files),
                "similarity": f"{best_score:.1f}%",
                "reason": best_reason,
            })

        self.duplicate_groups = new_groups

    def run(self, video_files):
        """主入口：提取关键帧 → 完全相同 → 编辑/重编码 → 合并连通组"""
        self.process_videos(video_files)
        if len(self.video_keyframe_hashes) < 2:
            log_info("视频数量不足，跳过匹配")
            return self.duplicate_groups

        self.find_identical_videos()
        self.find_edited_reencoded_videos()
        self._merge_connected_groups()

        log_success(f"视频去重完成: {len(self.duplicate_groups)} 组重复")
        return self.duplicate_groups


# ═══════════════════════════════════════════════════════════════
# 缩略图功能（保持不变，兼容 GUI）
# ═══════════════════════════════════════════════════════════════

def extract_video_thumbnail(video_path, thumbnail_cache_dir='results/thumbnails',
                            target_size=(200, 200)):
    """提取视频第1帧作为缩略图"""
    os.makedirs(thumbnail_cache_dir, exist_ok=True)
    h = hashlib.md5(video_path.encode()).hexdigest()
    p = os.path.join(thumbnail_cache_dir, f"{h}.jpg")
    if os.path.exists(p):
        try:
            return Image.open(p), p
        except Exception:
            pass
    try:
        cap = cv2.VideoCapture(video_path)
        ret, f = cap.read()
        cap.release()
        if not ret:
            return None, None
        img = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        img.thumbnail(target_size, Image.LANCZOS)
        img.save(p, 'JPEG', quality=85)
        return img, p
    except Exception:
        return None, None
