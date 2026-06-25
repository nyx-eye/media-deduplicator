# config.py
import os
from pathlib import Path

# ============ 文件扩展名配置 ============
SUPPORTED_IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff'
}
SUPPORTED_VIDEO_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.m4v', '.webm', '.mpg', '.mpeg'
}

# ============ 图片哈希配置 ============
IMAGE_HASH_ALGORITHM = 'phash'
IMAGE_HASH_SIZE = 8
IMAGE_HASH_THRESHOLD = 5

# ============ 视频配置 ============
VIDEO_FRAME_SKIP = 10                # 每 N 帧检测 1 次场景切换（性能关键参数）
VIDEO_FRAME_RESIZE = (128, 128)      # 哈希前缩放尺寸
VIDEO_MAX_FRAMES = 300               # 最大关键帧数
VIDEO_HASH_THRESHOLD = 12            # 帧哈希海明距离阈值
VIDEO_SIMILARITY_THRESHOLD = 60      # 序列相似度阈值 %

# ============ 场景检测 ============
HIST_BINS = [16, 16]
HIST_RANGE = [0, 180, 0, 256]
HIST_THRESHOLD = 0.50                # Bhattacharyya 距离阈值
SSIM_THRESHOLD = 0.55                # SSIM 结构相似度阈值
ABSDIFF_THRESHOLD = 18.0             # 第一级快速过滤阈值（像素均值差异）

# ============ 匹配引擎（倒排索引） ============
MIN_SHARED_FRAMES = 5                # 新增：触发候选的最小共享关键帧数
DURATION_RATIO_THRESHOLD = 0.3       # 新增：时长比阈值（<0.3 排除候选）
ENABLE_DURATION_PREFILTER = True     # 新增：启用时长预过滤

# ============ GPU 加速 ============
USE_GPU_DECODE = False               # 关闭 GPU 解码，统一使用 OpenCV 保证匹配精度
GPU_FRAME_SKIP = 5                   # 新增：GPU 模式下可更密集采样（ffmpeg scene 很快）
SAVE_KEYFRAMES = False               # 新增：是否保存关键帧到磁盘（仅缩略图模式需要）

# ============ 多线程配置 ============
MAX_WORKERS = 4

# ============ 输出配置 ============
RESULTS_DIR = 'results'
OUTPUT_JSON = os.path.join(RESULTS_DIR, 'duplicates.json')

Path(RESULTS_DIR).mkdir(exist_ok=True)
THUMBNAIL_CACHE_DIR = os.path.join(RESULTS_DIR, 'thumbnails')
Path(THUMBNAIL_CACHE_DIR).mkdir(exist_ok=True)