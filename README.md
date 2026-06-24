# Media Deduplicator

图片/视频去重工具，支持 GPU 加速（NVIDIA NVDEC），适配大规模媒体库（TB 级）的快速重复文件检测。

## 功能

### 图片去重
- 基于 pHash 感知哈希
- 按文件大小分组预筛，跳过 70% 唯一大小的图片
- `ProcessPoolExecutor` 多进程并行计算
- 支持完全相同内容的检测

### 视频去重
- GPU 加速（ffmpeg NVDEC 硬件解码）→ OpenCV 三级回退
- 场景切换检测：像素差分 → 结构相似度（SSIM）→ HSV 直方图 三级判定
- 倒排索引 + 锚点评分：O(K) 匹配，秒级完成
- 滑动窗口序列比对：解决关键帧偏移导致的对齐问题
- 连通分量合并：3 个以上相似视频合为一组
- 支持剪辑/压缩/重编码/变速版本的识别

### 断点续跑
- SQLite checkpoint，每 500 张图片/10 个视频写入一次
- 崩溃或暂停后重启自动从断点继续，不重算
- 切换文件夹自动清空旧 checkpoint

### GUI
- tkinter 图形界面
- 实时进度条 + ETA 剩余时间预估
- 实时显示已发现的重复组数量
- 暂停/继续/停止控制
- 缩略图预览 + 图片分辨率/格式 + 视频分辨率/帧率/时长
- 标记删除：重命名为 `.delete`，红色卡片显示，支持恢复
- JSON 结果导入/导出

## 安装

```bash
pip install -r requirements.txt
```

### GPU 加速（可选）

安装带 CUDA 支持的 ffmpeg：

```powershell
winget install Gyan.FFmpeg
```

## 使用

### GUI（推荐）

```bash
python gui.py
```

### 命令行

```bash
python main.py <目标文件夹>
```

## 配置

编辑 `config.py` 调整阈值和参数。

| 参数 | 说明 |
|------|------|
| `VIDEO_FRAME_SKIP` | CPU 路径每 N 帧检测 1 次场景切换 |
| `GPU_FRAME_SKIP` | GPU 路径跳帧间隔 |
| `VIDEO_HASH_THRESHOLD` | 帧哈希海明距离阈值 |
| `VIDEO_SIMILARITY_THRESHOLD` | 序列相似度阈值 % |
| `SSIM_THRESHOLD` | 场景切换结构相似度阈值 |
| `ABSDIFF_THRESHOLD` | 场景切换像素差异阈值 |
| `MIN_SHARED_FRAMES` | 触发候选的最小共享关键帧数 |
| `USE_GPU_DECODE` | 启用 ffmpeg NVDEC 硬件解码 |

## 输出

处理结果保存在 `results/duplicates.json`，可在 GUI 中通过「导入报告」加载。

## 依赖

- Python 3.8+
- Pillow, opencv-python, imagehash, scikit-image, numpy, tqdm
- ffmpeg（可选，GPU 加速，推荐 Gyan 或 BtbN build）
