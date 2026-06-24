# utils.py
import os
import sys
import cv2


def _safe_print(*args, **kwargs):
    """PyInstaller --windowed 下 sys.stdout 为 None，print 会崩溃"""
    try:
        print(*args, **kwargs)
    except Exception:
        pass

def get_file_size_mb(path):
    try:
        return os.path.getsize(path)/(1024*1024)
    except OSError:
        return 0

def is_image(path):
    from config import SUPPORTED_IMAGE_EXTENSIONS
    return os.path.splitext(path)[1].lower() in SUPPORTED_IMAGE_EXTENSIONS

def is_video(path):
    from config import SUPPORTED_VIDEO_EXTENSIONS
    return os.path.splitext(path)[1].lower() in SUPPORTED_VIDEO_EXTENSIONS

def hamming_distance(h1, h2):
    """优化版海明距离 — 直接对 numpy bool 数组做异或计数

    避免 bin(int(str())) 的双重字符串转换开销。
    imagehash 的 .hash 属性是 (8, 8) numpy bool 数组，
    异或后 .sum() 直接得到不同位数量。

    Args:
        h1, h2: imagehash ImageHash 对象
    Returns:
        int: 海明距离
    """
    # numpy bool 数组异或 → True 计数，零字符串转换，快 5-10x
    return (h1.hash.flatten() ^ h2.hash.flatten()).sum()

def get_video_metadata(video_path):
    """采集视频元数据：时长（秒）、分辨率、帧率

    Args:
        video_path: 视频文件路径
    Returns:
        dict: {'duration': float, 'width': int, 'height': int, 'fps': float}
              采集失败返回 None
    """
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap.release()
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 else 0
        cap.release()
        return {
            'duration': duration,
            'width': width,
            'height': height,
            'fps': fps
        }
    except Exception:
        return None

def log_info(s): _safe_print(f"[INFO] {s}")
def log_warning(s): _safe_print(f"[WARN] {s}")
def log_debug(s): pass
def log_success(s): _safe_print(f"[OK] {s}")