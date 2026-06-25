# Media Deduplicator

图片/视频去重工具，适配 TB 级媒体库。

## 功能

### 图片去重
- pHash 感知哈希，按文件大小分组预筛，跳过唯一大小的图片
- `ProcessPoolExecutor` 多进程并行

### 视频去重
- 三级场景检测：像素差分 → 结构相似度（SSIM）→ HSV 直方图
- 帧跳采样，兼容 CFR / VFR / 时间戳损坏视频
- 倒排索引 + 锚点评分 + 滑动窗口序列比对
- 连通分量合并，多视频归入一组
- 识别剪辑/压缩/重编码版本

### 断点续跑
- SQLite checkpoint，崩溃或暂停后自动续跑
- 切换文件夹自动清空旧数据

### GUI
- 暂停 / 继续 / 停止控制，收集结果独立按钮
- 实时进度条 + ETA 剩余时间 + 已发现重复组计数
- 缩略图预览：图片显示分辨率/格式，视频显示分辨率/帧率/时长
- 标记删除：文件移至 `扫描目录/delete/` 子目录，`原名(delete).后缀`，支持恢复
- 保留最高清晰度：自动标记删除组内低分辨率文件（视频区分剪辑版）
- 自动识别副本批量标记删除：图片 `(数字)` 命名 + 视频时长/大小相同
- JSON 导入/导出，标记删除状态持久化
- 关键帧 JSON 导入，支持换机重新匹配

## 安装

```bash
pip install -r requirements.txt
```

## 使用

```bash
python gui.py          # GUI（推荐）
python main.py 文件夹   # 命令行
```

## 配置

编辑 `config.py`：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `VIDEO_FRAME_SKIP` | 帧采样间隔 | 10 |
| `VIDEO_SIMILARITY_THRESHOLD` | 序列相似度阈值 % | 60 |
| `SSIM_THRESHOLD` | 场景切换相似度阈值 | 0.55 |
| `ABSDIFF_THRESHOLD` | 场景切换像素差异阈值 | 18 |
| `MIN_SHARED_FRAMES` | 触发候选的最小共享帧数 | 5 |

## 输出

`results/duplicates.json`，配合 `keyframes.json` 可导入 GUI。

## 依赖

- Python 3.8+
- Pillow, opencv-python, imagehash, scikit-image, numpy, tqdm
