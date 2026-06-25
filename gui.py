# gui.py
import sys
import os

# PyInstaller --windowed 模式下 stdout/stderr 为 None，任何 print/write 都会崩溃
# 必须在所有其他 import 之前重定向
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import subprocess
import json
from PIL import Image, ImageTk
import webbrowser
from hybrid_deduplicator import HybridMediaDeduplicator
from database import ResultsDatabase
from utils import get_file_size_mb, is_image, is_video

# 导入视频缩略图功能
try:
    from video_deduplicator import extract_video_thumbnail
    HAS_THUMBNAIL = True
except ImportError:
    HAS_THUMBNAIL = False

class MediaDeduplicatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Deduplicator v1.1")
        self.root.geometry("1500x950")
        self.root.configure(bg='#f0f0f0')
        
        self.groups = []
        self.failed_files = []
        self.current_group_index = 0
        self.thumbnail_cache = {}
        self.running = False
        self._paused = False
        self.scan_progress = 0
        
        self.create_widgets()
    
    def create_widgets(self):
        # 标题栏
        title_frame = tk.Frame(self.root, bg='#2196F3', height=50)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        
        tk.Label(title_frame, text="Media Deduplicator v1.1", 
                font=("微软雅黑", 14, "bold"), 
                bg='#2196F3', fg='white').pack(pady=12)
        
        # 选择文件夹区域
        frame_folder = tk.Frame(self.root, bg='#f0f0f0', pady=10)
        frame_folder.pack(fill=tk.X, padx=20)
        
        tk.Label(frame_folder, text="选择文件夹:", 
                font=("微软雅黑", 10), bg='#f0f0f0').pack(side=tk.LEFT)
        
        self.folder_path = tk.StringVar()
        self.folder_entry = tk.Entry(frame_folder, textvariable=self.folder_path, 
                                      width=50, font=("微软雅黑", 9))
        self.folder_entry.pack(side=tk.LEFT, padx=5)
        
        tk.Button(frame_folder, text="浏览...", command=self.select_folder,
                 bg='#4CAF50', fg='white', font=("微软雅黑", 9), 
                 padx=10, pady=3, cursor="hand2").pack(side=tk.LEFT, padx=5)
        
        tk.Button(frame_folder, text="导入报告", command=self.load_report,
                 bg='#FF9800', fg='white', font=("微软雅黑", 9), 
                 padx=10, pady=3, cursor="hand2").pack(side=tk.LEFT, padx=5)
        
        # 控制按钮区域
        control_frame = tk.Frame(self.root, bg='#f0f0f0', pady=8)
        control_frame.pack(fill=tk.X, padx=20)
        
        # ── 主控按钮行 ──
        ctrl_row = tk.Frame(control_frame, bg='#f0f0f0')
        ctrl_row.pack(fill=tk.X, pady=(0, 2))

        self.start_btn = tk.Button(ctrl_row, text="开始扫描去重",
                                   command=self.start_deduplication,
                                   bg='#2196F3', fg='white', font=("微软雅黑", 9),
                                   padx=10, pady=4, cursor="hand2")
        self.start_btn.pack(side=tk.LEFT, padx=3)

        self.pause_btn = tk.Button(ctrl_row, text="暂停",
                                    command=self.pause_deduplication,
                                    bg='#FF9800', fg='white', font=("微软雅黑", 9),
                                    padx=10, pady=4, cursor="hand2", state='disabled')
        self.pause_btn.pack(side=tk.LEFT, padx=3)

        self.resume_btn = tk.Button(ctrl_row, text="继续",
                                     command=self.resume_deduplication,
                                     bg='#4CAF50', fg='white', font=("微软雅黑", 9),
                                     padx=10, pady=4, cursor="hand2", state='disabled')
        self.resume_btn.pack(side=tk.LEFT, padx=3)

        self.stop_btn = tk.Button(ctrl_row, text="停止",
                                   command=self.stop_deduplication,
                                   bg='#f44336', fg='white', font=("微软雅黑", 9),
                                   padx=10, pady=4, cursor="hand2", state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=3)

        # ── 操作按钮行 ──
        op_row = tk.Frame(control_frame, bg='#f0f0f0')
        op_row.pack(fill=tk.X, pady=(0, 4))

        self.collect_btn = tk.Button(op_row, text="收集结果",
                                      command=self._gather_and_show,
                                      bg='#607D8B', fg='white', font=("微软雅黑", 9),
                                      padx=10, pady=4, cursor="hand2", state='disabled')
        self.collect_btn.pack(side=tk.LEFT, padx=3)

        self.best_btn = tk.Button(op_row, text="保留最高清晰度",
                                   command=self._auto_keep_best,
                                   bg='#607D8B', fg='white', font=("微软雅黑", 9),
                                   padx=10, pady=4, cursor="hand2", state='disabled')
        self.best_btn.pack(side=tk.LEFT, padx=3)

        self.numbered_btn = tk.Button(op_row, text="自动识别副本批量标记删除",
                                       command=self._auto_mark_copies,
                                       bg='#607D8B', fg='white', font=("微软雅黑", 9),
                                       padx=10, pady=4, cursor="hand2", state='disabled')
        self.numbered_btn.pack(side=tk.LEFT, padx=3)

        self.import_kf_btn = tk.Button(op_row, text="导入关键帧缓存",
                                        command=self._import_keyframes,
                                        bg='#455A64', fg='white', font=("微软雅黑", 9),
                                        padx=10, pady=4, cursor="hand2")
        self.import_kf_btn.pack(side=tk.LEFT, padx=3)

        # ── 进度 + 日志行 ──
        progress_row = tk.Frame(control_frame, bg='#f0f0f0')
        progress_row.pack(fill=tk.X, pady=4)

        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_row, variable=self.progress_var,
                                             mode='determinate', length=300)
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 8))
        self.progress_label = tk.Label(progress_row, text="0%",
                                        font=("微软雅黑", 9), bg='#f0f0f0', fg='#666', width=5)
        self.progress_label.pack(side=tk.LEFT)
        self.eta_label = tk.Label(progress_row, text="",
                                   font=("微软雅黑", 9), bg='#f0f0f0', fg='#888')
        self.eta_label.pack(side=tk.LEFT, padx=8)
        self.found_label = tk.Label(progress_row, text="",
                                     font=("微软雅黑", 9, "bold"),
                                     bg='#f0f0f0', fg='#e65100')
        self.found_label.pack(side=tk.LEFT, padx=8)
        self.status_label = tk.Label(progress_row, text="Ready",
                                     font=("微软雅黑", 9), bg='#f0f0f0', fg='#666')
        self.status_label.pack(side=tk.LEFT, padx=8)

        # 日志输出区
        self.log_text = scrolledtext.ScrolledText(control_frame, width=110, height=6,
                                                   font=("Consolas", 10), state="disabled",
                                                   bg="#222", fg="#d4ff54")
        self.log_text.pack(fill=tk.X, pady=(4, 0))
        
        # 统计信息
        stats_frame = tk.Frame(self.root, bg='#e0e0e0', pady=5)
        stats_frame.pack(fill=tk.X, padx=20, pady=5)
        
        self.stats_label = tk.Label(stats_frame, text="", 
                                    font=("微软雅黑", 9), bg='#e0e0e0', fg='#333')
        self.stats_label.pack()
        
        # 失败文件列表区
        fail_frame = tk.Frame(self.root, bg='#ffebee', pady=5)
        fail_frame.pack(fill=tk.X, padx=20, pady=3)
        tk.Label(fail_frame, text="读取失败的文件（双击路径复制）", font=("微软雅黑", 9, "bold"), bg='#ffebee', fg='#c62828').pack()
        self.failed_listbox = tk.Listbox(fail_frame, font=("微软雅黑", 8), height=3, bg="#fff", fg="#b71c1c", selectbackground="#ffccbc")
        self.failed_listbox.pack(fill=tk.X, padx=8)
        def on_copy_fail_file(event):
            sel = self.failed_listbox.curselection()
            if sel:
                path = self.failed_listbox.get(sel)
                self.root.clipboard_clear()
                self.root.clipboard_append(path)
                self.append_log(f"[路径已复制] {path}")
        self.failed_listbox.bind("<Double-Button-1>", on_copy_fail_file)
        
        # 主内容区域
        main_paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg='#f0f0f0')
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 左侧：结果列表
        left_frame = tk.Frame(main_paned, bg='white', relief=tk.RAISED, bd=1)
        main_paned.add(left_frame, width=400)
        
        tk.Label(left_frame, text="重复文件组列表 (点击查看)", 
                font=("微软雅黑", 10, "bold"), bg='white', fg='#333').pack(pady=8)
        
        list_frame = tk.Frame(left_frame, bg='white')
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.result_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                          font=("Consolas", 9), selectmode=tk.SINGLE,
                                          bg='#fafafa', selectbackground='#2196F3')
        self.result_listbox.pack(fill=tk.BOTH, expand=True)
        self.result_listbox.bind('<<ListboxSelect>>', self.on_select_group)
        scrollbar.config(command=self.result_listbox.yview)
        
        # 右侧：预览区域
        right_frame = tk.Frame(main_paned, bg='#e0e0e0', relief=tk.RAISED, bd=1)
        main_paned.add(right_frame, width=900)
        
        # 缩略图滚动区域
        canvas_frame = tk.Frame(right_frame, bg='#e0e0e0')
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        v_scrollbar = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        h_scrollbar = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.canvas = tk.Canvas(canvas_frame, bg='#e0e0e0', highlightthickness=0,
                                 yscrollcommand=v_scrollbar.set,
                                 xscrollcommand=h_scrollbar.set)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        v_scrollbar.config(command=self.canvas.yview)
        h_scrollbar.config(command=self.canvas.xview)
        
        self.thumb_frame = tk.Frame(self.canvas, bg='#e0e0e0')
        self.canvas.create_window((0, 0), window=self.thumb_frame, anchor="nw")
        
        self.thumb_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        
        def on_mousewheel(event):
            # 只在鼠标位于 canvas 区域内时滚轮才生效
            x = self.canvas.winfo_rootx()
            y = self.canvas.winfo_rooty()
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            if x <= event.x_root <= x + w and y <= event.y_root <= y + h:
                self.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        self.root.bind("<MouseWheel>", on_mousewheel)
        # 底部提示
        tip_frame = tk.Frame(self.root, bg='#fff9c4', pady=4)
        tip_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        tk.Label(tip_frame, text="提示: 双击缩略图打开文件 | 右键路径可复制 | 点击'导入报告'直接加载之前的结果", 
                font=("微软雅黑", 8), bg='#fff9c4', fg='#f57c00').pack()
    
    def append_log(self, msg):
        """线程安全日志：始终通过 after 调度到 UI 线程"""
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, f"{msg}\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        self.root.after(0, _do)

    def select_folder(self):
        folder = filedialog.askdirectory(title="选择要扫描的文件夹")
        if folder:
            if not os.path.isdir(folder):
                messagebox.showerror("路径无效", f"所选路径不存在或无法访问:\n{folder}")
                return
            self.folder_path.set(folder)
            self.append_log(f"已选择文件夹: {folder}")
            # 切换文件夹后提示旧结果可能失效
            if self.groups:
                self.clear_results()    
    def pause_deduplication(self):
        """暂停：设停止标志 → 等线程停 → 显示部分结果"""
        if not self.running:
            return
        self._paused = True
        self.running = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        self.pause_btn.config(state='disabled')
        self.update_status("正在暂停...")
        self.append_log("正在暂停，请等待当前任务结束...")

    def resume_deduplication(self):
        """继续：从 checkpoint 续跑"""
        self._paused = False
        self.running = True
        self._stop_event = threading.Event()
        self.pause_btn.config(state='normal')
        self.resume_btn.config(state='disabled')
        self.collect_btn.config(state='disabled')
        self.stop_btn.config(state='disabled')
        self.update_status("继续运行...")
        self.append_log("继续运行，从断点恢复...")
        self._eta_start_time = __import__('time').time()

        folder = self.folder_path.get()
        self._worker = threading.Thread(target=self.run_deduplication, args=(folder,))
        self._worker.daemon = True
        self._worker.start()

    def stop_deduplication(self):
        """完全停止：清空 checkpoint，重置一切"""
        self.running = False
        self._paused = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        self.update_status("正在停止...")
        self.append_log("正在停止...")

        # 清空 checkpoint
        try:
            from checkpoint import Checkpoint
            cp = Checkpoint()
            cp.cleanup()
            cp.close()
        except Exception:
            pass

        self.best_btn.config(state='disabled')
        self.numbered_btn.config(state='disabled')
        self.stop_progress()
        self.clear_results()

    # ====================== 【原版 100% 完整导入报告】 ======================
    def load_report(self):
        """导入之前的报告，无需重新扫描"""
        # 智能初始目录：优先 results/，不存在则用当前工作目录
        init_dir = "results" if os.path.isdir("results") else os.getcwd()
        json_path = filedialog.askopenfilename(
            title="选择之前的报告文件",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=init_dir
        )
        
        if not json_path:
            return
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            self.groups = report.get('groups', [])
            summary = report.get('summary', {})

            # 补回已标记删除的文件到 files 列表（导入后缩略图能显示红底卡片）
            for g in self.groups:
                for f in g.get("trashed", {}):
                    if f not in g["files"]:
                        g["files"].append(f)

            stats_text = f"导入报告: {summary.get('image_files', 0)} 图片 | {summary.get('video_files', 0)} 视频 | {len(self.groups)} 组重复 | 可节省 {summary.get('can_save_mb', 0):.2f} MB"
            self.stats_label.config(text=stats_text)
            
            self.populate_list(self.groups)
            self.update_status(f"导入成功! 发现 {len(self.groups)} 组重复文件")
            self.append_log(f"导入成功! 发现 {len(self.groups)} 组重复文件")
            
            messagebox.showinfo("导入成功", f"成功导入 {len(self.groups)} 组重复文件\n\n报告文件: {os.path.basename(json_path)}")
            
        except Exception as e:
            messagebox.showerror("导入失败", f"无法导入报告: {str(e)}")
            self.update_status(f"导入失败: {str(e)}")
            self.append_log(f"导入失败: {str(e)}")

    def start_deduplication(self):
        folder = self.folder_path.get()
        if not folder or not os.path.isdir(folder):
            self.update_status("错误: 请选择有效的文件夹")
            self.append_log("错误: 请选择有效的文件夹")
            return

        self.running = True
        self._paused = False
        self._stop_event = threading.Event()
        self.start_btn.config(state='disabled')
        self.pause_btn.config(state='normal')
        self.resume_btn.config(state='disabled')
        self.collect_btn.config(state='disabled')
        self.stop_btn.config(state='disabled')
        self.best_btn.config(state='disabled')
        self.numbered_btn.config(state='disabled')
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.eta_label.config(text="")
        self.found_label.config(text="")
        self.result_listbox.delete(0, tk.END)
        self.clear_thumbnails()

        # ETA 跟踪数据
        import time
        self._eta_start_time = time.time()

        # 清空日志（UI 线程直接操作 safe）
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.failed_listbox.delete(0, tk.END)
        self.failed_files.clear()
        self.update_status("正在扫描，请稍候...")
        self.append_log("启动扫描，请稍候...")

        self._worker = threading.Thread(target=self.run_deduplication, args=(folder,))
        self._worker.daemon = True
        self._worker.start()

    def run_deduplication(self, folder):
        """后台工作线程 — 所有 UI 操作必须通过 self.root.after() 调度"""
        try:
            self.append_log("正在初始化去重器...")
            self.failed_files.clear()
            self.root.after(0, lambda: self.failed_listbox.delete(0, tk.END))

            def fail_callback(file_path, errmsg=""):
                self.failed_files.append(file_path)
                self.append_log(f"[读取失败] {file_path} -- {errmsg}")
                self.root.after(0, lambda fp=file_path: self.failed_listbox.insert(tk.END, fp))

            # 进度回调：phase=scan/images/videos_extract/videos_match/report
            import time as _time
            def progress_callback(phase, current, total):
                if not self.running:
                    return

                # 实时发现重复：更新计数标签
                if phase == "found":
                    typ = "图片" if current > 0 else ""
                    self.root.after(0, lambda: self.found_label.config(
                        text=f"已发现 {current} 组重复"))
                    return

                now = _time.time()

                # 阶段 → 总进度区间映射
                phase_ranges = {
                    'scan':           (0, 10),
                    'images':         (10, 35),
                    'videos_extract': (35, 85),
                    'videos_match':   (85, 95),
                    'report':         (95, 100),
                }
                lo, hi = phase_ranges.get(phase, (0, 100))
                pct = lo + int((current / max(total, 1)) * (hi - lo))
                pct = min(pct, 100)

                # ETA：总进度反推 — 越靠后越准
                eta_str = ""
                if current > 0 and total > 0 and pct >= 5:
                    total_elapsed = now - self._eta_start_time
                    est_total = total_elapsed * 100 / max(pct, 1)
                    remaining = max(0, est_total - total_elapsed)
                    if remaining > 1:
                        if remaining < 60:
                            eta_str = f"剩余约 {remaining:.0f}秒"
                        else:
                            m = int(remaining // 60)
                            s = int(remaining % 60)
                            eta_str = f"剩余约 {m}分{s:02d}秒"

                phase_labels = {
                    'scan':           '正在扫描文件...',
                    'images':         '正在处理图片哈希...',
                    'videos_extract': '正在提取视频关键帧...',
                    'videos_match':   '正在匹配重复视频...',
                    'report':         '正在生成报告...',
                }
                label = phase_labels.get(phase, phase)
                self.update_progress(pct, f"{label} ({current}/{total})", eta_str)

            deduplicator = HybridMediaDeduplicator(
                folder, max_workers=4,
                stop_event=self._stop_event,
                progress_callback=progress_callback
            )
            deduplicator.image_dedup.fail_callback = fail_callback
            deduplicator.video_dedup.fail_callback = fail_callback
            self._deduplicator = deduplicator  # 保存引用供 _gather_and_show 使用

            self.update_status("扫描文件中...")
            success = deduplicator.run()

            # ── 暂停：只更新按钮，不收集结果 ──
            if self._paused:
                self.append_log("已暂停 — 可点击「收集结果」或「继续」")
                self.update_status("已暂停")
                self.root.after(0, lambda: self.pause_btn.config(state='disabled'))
                self.root.after(0, lambda: self.resume_btn.config(state='normal'))
                self.root.after(0, lambda: self.collect_btn.config(state='normal'))
                self.root.after(0, lambda: self.stop_btn.config(state='normal'))
                self.root.after(0, lambda: self.best_btn.config(state='normal'))
                self.root.after(0, lambda: self.numbered_btn.config(state='normal'))
                return

            # ── 完全停止 ──
            if not self.running:
                self.update_status("已停止")
                self.append_log("扫描已被用户停止")
                return

            if not success:
                self.update_status("扫描失败或没有文件")
                self.append_log("扫描失败或没有文件")
                return

            self.groups = deduplicator.all_duplicates
            summary = deduplicator.get_summary()

            progress_callback('report', 1, 1)
            self.append_log("完成，正在保存与生成报告...")
            self.update_status("保存结果中...")
            db = ResultsDatabase()
            db.export_to_json(self.groups, summary)
            self.append_log("已导出报告至 results 目录")

            stats_text = (
                f"统计: {summary['image_files']} 图片 | "
                f"{summary['video_files']} 视频 | "
                f"{len(self.groups)} 组重复 | "
                f"可节省 {summary.get('can_save_mb', 0):.2f} MB"
            )
            self.root.after(0, self.update_stats, stats_text)
            self.root.after(0, self.populate_list, self.groups)
            self.root.after(0, lambda: self.best_btn.config(state='normal'))
            self.root.after(0, lambda: self.numbered_btn.config(state='normal'))
            self.update_status(f"完成! 发现 {len(self.groups)} 组重复文件")
            self.append_log(f"全部完成！ 发现 {len(self.groups)} 组重复文件")

        except Exception as e:
            self.update_status(f"错误: {str(e)}")
            self.append_log(f"错误: {str(e)}")
        finally:
            if not self._paused:
                self.root.after(0, self.stop_progress)

    def update_progress(self, value, message, eta_text=""):
        """线程安全进度更新（含 ETA 剩余时间）"""
        self.root.after(0, lambda: self.progress_var.set(value))
        self.root.after(0, lambda: self.progress_label.config(text=f"{value}%"))
        self.root.after(0, lambda: self.eta_label.config(text=eta_text))
        self.update_status(message)
    
    def populate_list(self, groups):
        self.result_listbox.delete(0, tk.END)
        self.groups = groups

        for idx, group in enumerate(groups, 1):
            file_type = "[图片]" if group['type'] == 'image' else "[视频]"
            count = group['count']
            similarity = group['similarity']
            if group['files']:
                first_name = os.path.basename(group['files'][0])
                files_info = f" ({first_name})"
            else:
                files_info = ""
            reason = group.get("reason", "")
            self.result_listbox.insert(tk.END, f"{idx:3d}. {file_type} {similarity} — {reason} — {count} 个文件{files_info}")

            # 根据标记删除状态设置颜色
            trashed = group.get("trashed", {})
            trashed_count = len(trashed)
            remaining = count - trashed_count
            if remaining == 1 and trashed_count > 0:
                fg = "#2e7d32"  # 绿色：还剩1个
            elif trashed_count > 0:
                fg = "#e65100"  # 橙色：部分标记删除
            else:
                fg = "#000000"  # 默认黑色
            self.result_listbox.itemconfig(idx - 1, foreground=fg,
                                           selectforeground=fg)
    
    def on_select_group(self, event):
        selection = self.result_listbox.curselection()
        if not selection:
            return
        
        self.current_group_index = selection[0]
        group = self.groups[self.current_group_index]
        
        self.clear_thumbnails()
        self.thumbnail_cache = {}
        trashed = group.get("trashed", {})
        for idx, file_path in enumerate(group["files"]):
            self.add_thumbnail(file_path, idx, trashed)
    
    def add_thumbnail(self, file_path, idx, trashed=None):
        """添加缩略图卡片。trashed = {原路径: .delete路径} 标记删除状态"""
        trashed_path = trashed.get(file_path) if trashed else None
        is_trashed = trashed_path is not None
        display_path = trashed_path if is_trashed else file_path
        card_bg = '#ffcdd2' if is_trashed else 'white'
        fg_color = '#333'

        frame = tk.Frame(self.thumb_frame, bg=card_bg, relief=tk.RAISED, bd=2)
        frame.pack(side=tk.LEFT, padx=8, pady=8, fill=tk.Y)

        # 缩略图 + 信息
        info = None
        if is_image(file_path):
            info = self._add_image_thumbnail(frame, display_path)
        elif is_video(file_path):
            info = self._add_video_thumbnail(frame, display_path)
        else:
            info = self._add_generic_thumbnail(frame, display_path)

        # 文件名
        tk.Label(frame, text=os.path.basename(display_path),
                font=("微软雅黑", 8, "bold"), bg=card_bg, fg=fg_color,
                wraplength=200).pack(pady=(5, 0))

        # 文件大小
        size_mb = get_file_size_mb(display_path)
        tk.Label(frame, text=f"{size_mb:.1f} MB",
                font=("微软雅黑", 8), bg=card_bg, fg='#666').pack()

        # 分辨率 / 格式 / 视频信息
        if info:
            parts = []
            if isinstance(info, tuple):
                if len(info) == 3:  # 图片: (w, h, fmt)
                    parts.append(f"{info[0]}×{info[1]}")
                    parts.append(info[2])
                elif len(info) == 4:  # 视频: (w, h, fps, dur)
                    parts.append(f"{info[0]}×{info[1]}")
                    if info[2]:
                        parts.append(f"{info[2]:.0f}fps")
                    if info[3]:
                        m, s = divmod(int(info[3]), 60)
                        parts.append(f"{m}:{s:02d}")
            tk.Label(frame, text="  ".join(parts),
                    font=("微软雅黑", 7), bg=card_bg, fg='#555').pack()

        # 标记删除标签（区分来源）
        if is_trashed:
            group = self.groups[self.current_group_index]
            actions = group.get("auto_actions", {})
            if file_path in actions.get("keep_best", []):
                tag_text, tag_color = "低清晰度 — 已标记", "#e65100"
            elif file_path in actions.get("mark_copies", []):
                tag_text, tag_color = "副本 — 已标记", "#6A1B9A"
            else:
                tag_text, tag_color = "已标记删除", "#c62828"
            tk.Label(frame, text=tag_text, font=("微软雅黑", 7, "bold"),
                    bg=card_bg, fg=tag_color).pack()

        # 路径
        path_label = tk.Label(frame, text=display_path, font=("微软雅黑", 6),
                              bg='#f0f0f0', fg='blue', cursor="hand2",
                              wraplength=200, justify=tk.LEFT)
        path_label.pack(pady=(3, 0), padx=5, fill=tk.X)
        path_label.bind("<Button-3>", lambda e: self._copy_path(display_path))
        path_label.bind("<Double-Button-1>",
                        lambda e, p=display_path: self.open_file(p))

        # 按钮
        btn_frame = tk.Frame(frame, bg=card_bg)
        btn_frame.pack(pady=5)

        tk.Button(btn_frame, text="打开",
                  command=lambda p=display_path: self.open_file(p),
                  bg='#2196F3', fg='white', font=("微软雅黑", 7),
                  padx=8, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)

        tk.Button(btn_frame, text="打开文件夹",
                  command=lambda p=display_path: self.open_folder(p),
                  bg='#FF9800', fg='white', font=("微软雅黑", 7),
                  padx=8, pady=2, cursor="hand2", width=8).pack(side=tk.LEFT, padx=2)

        if is_trashed:
            tk.Button(btn_frame, text="恢复",
                      command=lambda fp=file_path: self._restore_file(fp, frame),
                      bg='#4CAF50', fg='white', font=("微软雅黑", 7),
                      padx=6, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)
        else:
            tk.Button(btn_frame, text="标记删除",
                      command=lambda fp=file_path, fr=frame: self._mark_delete(fp, fr),
                      bg='#f44336', fg='white', font=("微软雅黑", 7),
                      padx=6, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)

        self.thumb_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _copy_path(self, path):
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        self.update_status(f"已复制路径: {os.path.basename(path)}")
        self.append_log(f"已复制路径: {os.path.basename(path)}")

    def _mark_delete(self, file_path, old_frame, rebuild=True, action=None):
        """标记删除：移动到 scan_root/delete/ 子目录，重命名为 原名(delete).后缀"""
        import shutil
        scan_root = self.folder_path.get()
        try:
            rel = os.path.relpath(file_path, scan_root)
        except ValueError:
            rel = os.path.basename(file_path)
        rel_dir = os.path.dirname(rel)
        fname = os.path.basename(file_path)
        name, ext = os.path.splitext(fname)
        trash_dir = os.path.join(scan_root, "delete", rel_dir)
        os.makedirs(trash_dir, exist_ok=True)
        target = os.path.join(trash_dir, f"{name}(delete){ext}")
        counter = 1
        while os.path.exists(target):
            target = os.path.join(trash_dir, f"{name}({counter})(delete){ext}")
            counter += 1
        try:
            shutil.move(file_path, target)
        except Exception as e:
            messagebox.showerror("标记删除失败", str(e))
            return
        self._update_trashed(file_path, target)
        # 记录 auto_actions（移到成功后执行，避免假阳性）
        if action:
            g = self.groups[self.current_group_index]
            g.setdefault("auto_actions", {}).setdefault(action, []).append(file_path)
        if old_frame:
            old_frame.destroy()
        if rebuild:
            self._rebuild_card()
        self.append_log(f"[已标记删除] {os.path.basename(file_path)}")
        self.update_status(f"已标记删除: {os.path.basename(file_path)}")

    def _restore_file(self, file_path, old_frame):
        """恢复：移回原位（冲突加(恢复)） + 清理空目录"""
        import shutil
        scan_root = self.folder_path.get()
        group = self.groups[self.current_group_index]
        # 用 trashed dict 找实际路径
        trashed_path = group.get("trashed", {}).get(file_path)
        if not trashed_path or not os.path.exists(trashed_path):
            messagebox.showerror("恢复失败", "找不到标记删除的文件")
            return

        # 目标：原路径
        restored = file_path
        if os.path.exists(restored):
            # 冲突 → 加 (恢复)
            d, n = os.path.split(file_path)
            name, ext = os.path.splitext(n)
            counter = 1
            while counter <= 100:
                restored = os.path.join(d, f"{name} (恢复{counter}){ext}")
                if not os.path.exists(restored):
                    break
                counter += 1
            if counter > 100:
                messagebox.showerror("恢复失败", "冲突过多，请手动处理")
                return

        try:
            os.makedirs(os.path.dirname(restored), exist_ok=True)
            shutil.move(trashed_path, restored)
        except Exception as e:
            messagebox.showerror("恢复失败", str(e))
            return

        # 路径变化 → 更新 group["files"]
        if restored != file_path:
            group["files"][group["files"].index(file_path)] = restored

        # 清理空目录
        trash_dir = os.path.join(scan_root, "delete")
        try:
            old_dir = os.path.dirname(trashed_path)
            while old_dir and old_dir.startswith(trash_dir) and old_dir != trash_dir:
                if not os.listdir(old_dir):
                    os.rmdir(old_dir)
                    old_dir = os.path.dirname(old_dir)
                else:
                    break
        except Exception:
            pass

        self._update_trashed(file_path, None)
        # 清理 auto_actions
        group_a = self.groups[self.current_group_index]
        actions = group_a.get("auto_actions", {})
        for aname, files in list(actions.items()):
            if file_path in files:
                files.remove(file_path)
                if not files:
                    del actions[aname]
        if not group_a.get("auto_actions"):
            group_a.pop("auto_actions", None)
        old_frame.destroy()
        self._rebuild_card()
        self.append_log(f"[已恢复] {os.path.basename(file_path)}")
        self.update_status(f"已恢复: {os.path.basename(file_path)}")

    def _update_trashed(self, file_path, trashed_path):
        """更新当前组的 trashed 字段 + 重写 JSON"""
        group = self.groups[self.current_group_index]
        if "trashed" not in group:
            group["trashed"] = {}
        if trashed_path:
            group["trashed"][file_path] = trashed_path
        else:
            group["trashed"].pop(file_path, None)
            if not group["trashed"]:
                del group["trashed"]
        try:
            import json as _json
            db_path = "results/duplicates.json"
            if os.path.exists(db_path):
                with open(db_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                data["groups"] = self.groups
                with open(db_path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _rebuild_card(self):
        """重新显示当前组的所有缩略图卡片 + 刷新列表颜色"""
        self.clear_thumbnails()
        self.thumbnail_cache = {}
        group = self.groups[self.current_group_index]
        trashed = group.get("trashed", {})
        for idx, file_path in enumerate(group["files"]):
            self.add_thumbnail(file_path, idx, trashed)
        # 刷新列表项颜色
        self.populate_list(self.groups)
        self.result_listbox.selection_set(self.current_group_index)
        self.result_listbox.see(self.current_group_index)

    def _gather_and_show(self):
        """收集当前发现的重复并显示"""
        if not hasattr(self, '_deduplicator') or not self._deduplicator:
            messagebox.showinfo("提示", "没有可收集的结果")
            return
        self.update_status("正在收集结果...")
        self.append_log("正在收集当前发现的重复...")
        dedup = self._deduplicator
        dedup.gather_partial_results()
        self.groups = dedup.all_duplicates
        summary = dedup.get_summary()

        if self.groups:
            self.populate_list(self.groups)
            stats_text = (
                f"暂停 — 已发现 {len(self.groups)} 组重复 | "
                f"图片: {summary['image_files']} | "
                f"视频: {summary['video_files']}"
            )
            self.update_stats(stats_text)
        self.update_status("已暂停 — 可继续或停止")
        self.append_log(f"已收集 {len(self.groups)} 组重复")
        self.collect_btn.config(state='disabled')

    def _import_keyframes(self):
        """导入关键帧缓存：复制到 results/keyframes.json 供扫描时使用"""
        import shutil
        json_path = filedialog.askopenfilename(
            title="选择关键帧缓存文件",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="results"
        )
        if not json_path:
            return
        try:
            os.makedirs("results", exist_ok=True)
            dest = os.path.join("results", "keyframes.json")
            if os.path.abspath(json_path) != os.path.abspath(dest):
                shutil.copy2(json_path, dest)
            import json as _json
            with open(dest, "r", encoding="utf-8") as f:
                kf = _json.load(f)
            self.update_status(f"已加载 {len(kf)} 个视频的关键帧缓存")
            self.append_log(f"已加载关键帧缓存: {len(kf)} 个视频（将在扫描时跳过未变化的文件）")
            self.import_kf_btn.config(text="取消导入", command=self._cancel_keyframes,
                                       bg='#c62828', fg='white')
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def _cancel_keyframes(self):
        """删除关键帧缓存，恢复按钮"""
        cache_path = os.path.join("results", "keyframes.json")
        if os.path.exists(cache_path):
            os.remove(cache_path)
        self.update_status("已取消关键帧缓存")
        self.append_log("已取消关键帧缓存，扫描时将重新提取所有视频")
        self.import_kf_btn.config(text="导入关键帧缓存", command=self._import_keyframes,
                                   bg='#455A64', fg='white')

    def _find_group_index(self, file_path):
        """找到文件所属的重复组索引"""
        for idx, g in enumerate(self.groups):
            if file_path in g["files"]:
                return idx
        return None

    def _batch_mark(self, file_list, action=None):
        """批量标记删除，只刷新一次"""
        saved_idx = self.current_group_index
        for fp in file_list:
            idx = self._find_group_index(fp)
            if idx is not None:
                self.current_group_index = idx
                self._mark_delete(fp, None, rebuild=False, action=action)
        self.current_group_index = saved_idx
        self.populate_list(self.groups)
        self._rebuild_card()
        self.append_log(f"[批量] 已标记删除 {len(file_list)} 个文件")

    def _auto_keep_best(self):
        """保留最高清晰度：标记删除组内低清晰度的文件（跳过已执行的组）"""
        to_delete = []
        to_delete_set = set()
        for g in self.groups:
            trashed = g.get("trashed", {})
            actions = g.get("auto_actions", {})
            if actions.get("keep_best"):
                continue
            candidates = [f for f in g["files"] if f not in trashed]
            if len(candidates) <= 1:
                continue

            if "image" in g["type"]:
                best_f = None
                best_px = -1
                for f in candidates:
                    try:
                        img = Image.open(f)
                        px = img.size[0] * img.size[1]
                        if px > best_px:
                            best_px = px
                            best_f = f
                    except Exception:
                        pass
                if best_f:
                    for f in candidates:
                        if f != best_f:
                            to_delete.append(f)

            elif "video" in g["type"]:
                from utils import get_video_metadata
                metas = {}
                max_dur = 0
                for f in candidates:
                    m = get_video_metadata(f)
                    if m:
                        metas[f] = m
                        max_dur = max(max_dur, m.get("duration", 0))
                if max_dur <= 0:
                    continue
                # 找基准时长内的高分辨率
                same_dur = [f for f in candidates
                            if f in metas and abs(metas[f].get("duration", 0) - max_dur) <= 2]
                if len(same_dur) <= 1:
                    continue
                best_f = max(same_dur, key=lambda f: metas[f].get("width", 0) * metas[f].get("height", 0))
                for f in same_dur:
                    if f != best_f:
                        to_delete.append(f)

        if to_delete:
            self._batch_mark(to_delete, "keep_best")
            messagebox.showinfo("完成", f"已标记删除 {len(to_delete)} 个低清晰度文件")
        else:
            messagebox.showinfo("提示", "没有可标记删除的文件")

    def _auto_mark_copies(self):
        """自动识别副本：图片名字带(数字)+视频时长/大小相同的"""
        import re
        to_delete = []
        to_delete_set = set()
        numbered_pattern = re.compile(r"^(.*?)\s*\(\d+\)(\.[^.]+)$")
        from utils import get_video_metadata

        for g in self.groups:
            actions = g.get("auto_actions", {})
            if actions.get("mark_copies"):
                continue
            trashed = g.get("trashed", {})
            candidates = [f for f in g["files"] if f not in trashed]
            if len(candidates) <= 1:
                continue

            if "image" in g["type"]:
                for f in candidates:
                    d, n = os.path.split(f)
                    m = numbered_pattern.match(n)
                    if not m:
                        continue
                    sibling = os.path.join(d, m.group(1) + m.group(2))
                    if sibling in candidates and sibling != f:
                        try:
                            if os.path.getsize(f) == os.path.getsize(sibling):
                                to_delete.append(f)
                        except OSError:
                            pass

            elif "video" in g["type"]:
                metas = {}
                for f in candidates:
                    m = get_video_metadata(f)
                    if m:
                        metas[f] = m
                # 按时长+大小分组，相同的标记为副本
                for i, f1 in enumerate(candidates):
                    if f1 not in metas:
                        continue
                    dur1 = metas[f1].get("duration", 0)
                    try:
                        sz1 = os.path.getsize(f1)
                    except OSError:
                        continue
                    for f2 in candidates[i + 1:]:
                        if f2 not in metas:
                            continue
                        if abs(metas[f2].get("duration", 0) - dur1) <= 2:
                            try:
                                if abs(os.path.getsize(f2) - sz1) < max(sz1, os.path.getsize(f2)) * 0.05:
                                    # 保留更短路径的，标记另一个
                                    if len(f2) < len(f1):
                                        to_delete.append(f1)
                                    elif f1 not in to_delete:
                                        to_delete.append(f2)
                            except OSError:
                                pass

        if to_delete:
            self._batch_mark(to_delete, "mark_copies")
            messagebox.showinfo("完成", f"已自动标记 {len(to_delete)} 个副本文件")
        else:
            messagebox.showinfo("提示", "没有找到可自动标记的副本文件")

    def _add_image_thumbnail(self, frame, file_path):
        """加载图片缩略图，返回 (w, h, fmt) 或 None"""
        try:
            img = Image.open(file_path)
            w, h = img.size
            fmt = img.format or "未知"
            img.thumbnail((160, 160))
            photo = ImageTk.PhotoImage(img)
            key = f"img_{len(self.thumbnail_cache)}"
            self.thumbnail_cache[key] = photo
            label = tk.Label(frame, image=photo, bg='white', cursor="hand2")
            label.pack(padx=8, pady=8)
            label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
            return w, h, fmt
        except Exception:
            tk.Label(frame, text="[加载失败]", font=("微软雅黑", 10),
                    bg='white', fg='red').pack(padx=8, pady=20)
            return None

    def _add_video_thumbnail(self, frame, file_path):
        """加载视频缩略图，返回 (w, h, fps, duration) 或 None"""
        meta = None
        try:
            from utils import get_video_metadata
            meta = get_video_metadata(file_path)
        except Exception:
            pass

        if HAS_THUMBNAIL:
            try:
                img, cache_path = extract_video_thumbnail(file_path)
                if img:
                    photo = ImageTk.PhotoImage(img)
                    key = f"video_{len(self.thumbnail_cache)}"
                    self.thumbnail_cache[key] = photo
                    label = tk.Label(frame, image=photo, bg='#333', cursor="hand2")
                    label.pack(padx=8, pady=8)
                    label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
                    if meta:
                        return meta["width"], meta["height"], meta["fps"], meta["duration"]
                    return None
            except Exception:
                pass

        video_label = tk.Label(frame, text="[VIDEO]", font=("微软雅黑", 20, "bold"),
                               bg='#333', fg='white', cursor="hand2")
        video_label.pack(padx=8, pady=20)
        video_label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
        frame.configure(bg='#333')
        if meta:
            return meta["width"], meta["height"], meta["fps"], meta["duration"]
        return None

    def _add_generic_thumbnail(self, frame, file_path):
        tk.Label(frame, text="[FILE]", font=("微软雅黑", 20, "bold"),
                bg='#666', fg='white').pack(padx=8, pady=20)
        frame.configure(bg='#666')
        return None
    
    def open_folder(self, file_path):
        """打开文件所在文件夹并选中该文件"""
        # 前置检查：文件或目录是否存在
        if not os.path.exists(file_path):
            folder_candidate = os.path.dirname(file_path)
            if os.path.isdir(folder_candidate):
                # 文件已删除，但所在文件夹还在 → 打开文件夹
                try:
                    os.startfile(folder_candidate)
                except Exception:
                    subprocess.run(['explorer', folder_candidate])
                return
            else:
                messagebox.showwarning("路径不存在",
                    f"文件及其所在目录均已不存在:\n{file_path}")
                return

        folder_path = os.path.dirname(file_path)
        try:
            # /select 参数：打开资源管理器并选中该文件
            subprocess.run(['explorer', '/select,', os.path.normpath(file_path)])
        except Exception:
            try:
                os.startfile(folder_path)
            except Exception:
                messagebox.showwarning("打开失败",
                    f"无法打开文件夹:\n{folder_path}")
    
    def open_file(self, file_path):
        """用系统默认程序打开文件"""
        if not os.path.exists(file_path):
            messagebox.showwarning("文件不存在",
                f"文件可能已被移动或删除:\n{file_path}")
            return

        try:
            # 方式 1：Windows 原生 API，自动调用关联程序
            os.startfile(os.path.normpath(file_path))
        except Exception:
            try:
                # 方式 2：cmd /c start "" "path" — 等同于双击文件
                subprocess.run(
                    ['cmd', '/c', 'start', '', os.path.normpath(file_path)],
                    shell=False)
            except Exception:
                try:
                    # 方式 3：file:// 协议（需要绝对路径）
                    abs_path = os.path.abspath(file_path).replace('\\', '/')
                    webbrowser.open(f'file:///{abs_path}')
                except Exception:
                    messagebox.showwarning("打开失败",
                        f"无法打开文件:\n{file_path}\n\n请检查是否有关联程序支持此文件类型")
    
    def clear_thumbnails(self):
        for widget in self.thumb_frame.winfo_children():
            widget.destroy()
        self.thumbnail_cache = {}
    
    def stop_progress(self):
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.eta_label.config(text="")
        self.found_label.config(text="")
        self.start_btn.config(state='normal')
        self.pause_btn.config(state='disabled')
        self.resume_btn.config(state='disabled')
        self.collect_btn.config(state='disabled')
        self.stop_btn.config(state='disabled')
    
    def update_status(self, message):
        """线程安全状态更新：始终通过 after 调度到 UI 线程"""
        self.root.after(0, lambda m=message: self.status_label.config(text=m))
    
    def update_stats(self, text):
        self.stats_label.config(text=text)
    
    def clear_results(self):
        self.result_listbox.delete(0, tk.END)
        self.clear_thumbnails()
        self.groups = []
        self.update_status("结果已清空")
        self.update_stats("")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = MediaDeduplicatorGUI(root)
    root.mainloop()