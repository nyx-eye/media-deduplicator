# Media Deduplicator v1.1

图片/视频去重工具，适配 TB 级媒体库。识别以下重复类型：

**图片 / Images**：内容完全相同的图片（pHash 指纹一致），按文件大小分组预筛。

**视频 / Videos**：
- 完全相同 / Identical — 关键帧序列 ≥95% 一致
- 压缩/重编码 / Compressed — 不同分辨率或编码器，内容相同
- 轻度剪辑 / Light Edit — 部分片段被裁剪
- 剪辑+压缩 / Edited+Compressed — 裁剪且重新编码
- 跨分辨率压缩 / Cross-Resolution — 极端压缩导致关键帧偏移较大（精确共享 ≥20 帧 + 时长比 ≥80%）
- 连通分量合并 — 3 个以上相似视频自动归入同一组

Image/video deduplication tool for TB-scale libraries. Detects: identical images (pHash), identical/re-encoded/edited/cross-resolution videos with connected component merging.

## 安装 / Install

```bash
pip install -r requirements.txt
```

## 使用 / Usage

```bash
python gui.py
```

## 界面说明 / Interface

### 按钮 / Buttons

| 按钮 Button | 说明 Description |
|-------------|-----------------|
| 开始扫描去重 / Start Scan | 选择文件夹后开始处理 |
| 暂停 / Pause | 暂停当前任务 |
| 继续 / Resume | 从断点恢复 |
| 停止 / Stop | 完全停止并清空断点 |
| 收集结果 / Collect Results | 暂停后显示已发现的重复组 |
| 保留最高清晰度 / Keep Best Quality | 自动标记删除组内低分辨率文件（视频排除剪辑版） |
| 自动识别副本批量标记删除 / Auto Mark Duplicates | 自动标记副本文件 |
| 导入关键帧缓存 / Import Keyframe Cache | 导入上次扫描的缓存，跳过未变化视频 |
| 导入报告 / Import Report | 加载已保存的 `duplicates.json` |

### 重复组列表 / Group List

- 选中组 → 右侧显示缩略图 / Select group → thumbnails on right
- **橙色/Orange**：组内有文件已标记删除 / some files marked
- **绿色/Green**：组内仅剩一个未标记文件 / only one file remains

### 缩略图卡片 / Thumbnail Cards

- 显示分辨率、格式（图片）/ 帧率、时长（视频）
  Shows resolution, format (image) / fps, duration (video)
- **[打开/Open]**：用默认程序打开文件
- **[打开文件夹/Open Folder]**：在资源管理器中定位文件
- **[标记删除/Mark Delete]**：移到 `扫描目录/delete/`，`原名(delete).后缀`
- **[恢复/Restore]**：将标记删除的文件移回原位
- **红底卡片/Red background**：已标记删除 / marked for deletion
- 标签区分来源 / Tags: 橙色「低清晰度」/ 紫色「副本」/ 红色「已标记删除」
- 滚轮横向浏览 / Mouse wheel to scroll horizontally

### 快捷操作 / Shortcuts

- **右键路径 / Right-click path**：复制文件路径 / copy path
- **双击缩略图/路径 / Double-click**：打开文件 / open file

## 配置 / Config

编辑 `config.py`：

| 参数 Parameter | 默认值 Default | 说明 Description |
|---------------|---------------|-----------------|
| `VIDEO_FRAME_SKIP` | 10 | 帧采样间隔 / frame sampling interval |
| `VIDEO_SIMILARITY_THRESHOLD` | 60 | 视频序列相似度阈值 % |
| `SSIM_THRESHOLD` | 0.55 | 场景切换结构相似度阈值 |
| `MIN_SHARED_FRAMES` | 5 | 匹配候选最少共享帧数 / min shared frames |

## 输出 / Output

- `results/duplicates.json` — 重复组结果 / duplicate groups (含标记删除状态)
- `results/keyframes.json` — 视频关键帧缓存 / keyframe cache (二次扫描加速)
