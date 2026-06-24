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
        self.root.title("Media Deduplicator - 增强版")
        self.root.geometry("1500x950")
        self.root.configure(bg='#f0f0f0')
        
        self.groups = []
        self.failed_files = []
        self.current_group_index = 0
        self.thumbnail_cache = {}
        self.running = False
        self.scan_progress = 0
        
        self.create_widgets()
    
    def create_widgets(self):
        # 标题栏
        title_frame = tk.Frame(self.root, bg='#2196F3', height=50)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        
        tk.Label(title_frame, text="Media Deduplicator 1.0 - 图片视频去重工具", 
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
        
        self.start_btn = tk.Button(control_frame, text="开始扫描去重", 
                                   command=self.start_deduplication,
                                   bg='#2196F3', fg='white', font=("微软雅黑", 10, "bold"),
                                   padx=15, pady=5, cursor="hand2")
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = tk.Button(control_frame, text="停止", 
                                   command=self.stop_deduplication,
                                   bg='#f44336', fg='white', font=("微软雅黑", 9),
                                   padx=10, pady=5, cursor="hand2", state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        # 日志输出区
        self.log_text = scrolledtext.ScrolledText(control_frame, width=68, height=8, font=("Consolas", 10), state="disabled", bg="#222", fg="#d4ff54")
        self.log_text.pack(side=tk.LEFT, padx=10)

        # 进度条
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(control_frame, variable=self.progress_var,
                                             mode='determinate', length=300)
        self.progress_bar.pack(side=tk.LEFT, padx=10)
        self.progress_label = tk.Label(control_frame, text="0%",
                                        font=("微软雅黑", 9), bg='#f0f0f0', fg='#666', width=5)
        self.progress_label.pack(side=tk.LEFT)

        # ETA 剩余时间标签
        self.eta_label = tk.Label(control_frame, text="",
                                   font=("微软雅黑", 9), bg='#f0f0f0', fg='#888')
        self.eta_label.pack(side=tk.LEFT, padx=5)

        self.status_label = tk.Label(control_frame, text="Ready", 
                                     font=("微软雅黑", 9), bg='#f0f0f0', fg='#666')
        self.status_label.pack(side=tk.LEFT, padx=10)
        
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
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.canvas.bind("<MouseWheel>", on_mousewheel)
        
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
    def stop_deduplication(self):
        self.running = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        self.update_status("正在停止...")
        self.append_log("已请求停止，请等待当前任务结束...")
        self.stop_btn.config(state='disabled')

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
        self._stop_event = threading.Event()
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.eta_label.config(text="")
        self.result_listbox.delete(0, tk.END)
        self.clear_thumbnails()

        # ETA 跟踪数据
        import time
        self._eta_start_time = time.time()  # 总开始时间

        # 清空日志（UI 线程直接操作 safe）
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.failed_listbox.delete(0, tk.END)
        self.failed_files.clear()
        self.update_status("正在扫描，请稍候...")
        self.append_log("启动扫描，请稍候...")

        thread = threading.Thread(target=self.run_deduplication, args=(folder,))
        thread.daemon = True
        thread.start()

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

            self.update_status("扫描文件中...")
            success = deduplicator.run()

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
            self.update_status(f"完成! 发现 {len(self.groups)} 组重复文件")
            self.append_log(f"全部完成！ 发现 {len(self.groups)} 组重复文件")

        except Exception as e:
            self.update_status(f"错误: {str(e)}")
            self.append_log(f"错误: {str(e)}")
        finally:
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
                if len(first_name) > 25:
                    first_name = first_name[:22] + "..."
                files_info = f" ({first_name})"
            else:
                files_info = ""
            self.result_listbox.insert(tk.END, f"{idx:3d}. {file_type} {similarity} - {count} 个文件{files_info}")
    
    def on_select_group(self, event):
        selection = self.result_listbox.curselection()
        if not selection:
            return
        
        self.current_group_index = selection[0]
        group = self.groups[self.current_group_index]
        
        self.clear_thumbnails()
        self.thumbnail_cache = {}
        for idx, file_path in enumerate(group['files']):
            self.add_thumbnail(file_path, idx)
    
    # ====================== 【原版 100% 完整缩略图】 ======================
    def add_thumbnail(self, file_path, idx):
        frame = tk.Frame(self.thumb_frame, bg='white', relief=tk.RAISED, bd=2)
        frame.pack(side=tk.LEFT, padx=8, pady=8, fill=tk.Y)
        
        if is_image(file_path):
            self._add_image_thumbnail(frame, file_path)
        elif is_video(file_path):
            self._add_video_thumbnail(frame, file_path)
        else:
            self._add_generic_thumbnail(frame, file_path)
        
        fname = os.path.basename(file_path)
        if len(fname) > 25:
            fname = fname[:22] + "..."
        tk.Label(frame, text=fname, font=("微软雅黑", 8, "bold"), 
                bg='white', fg='#333', wraplength=160).pack(pady=(5,0))
        
        size_mb = get_file_size_mb(file_path)
        tk.Label(frame, text=f"{size_mb:.1f} MB", font=("微软雅黑", 8), 
                bg='white', fg='#666').pack()
        
        path_text = file_path
        if len(path_text) > 40:
            path_text = "..." + path_text[-37:]
        
        path_label = tk.Label(frame, text=path_text, font=("微软雅黑", 7), 
                              bg='#f0f0f0', fg='blue', cursor="hand2",
                              wraplength=170, justify=tk.CENTER)
        path_label.pack(pady=(3,0), padx=5, fill=tk.X)
        
        def copy_path(event):
            self.root.clipboard_clear()
            self.root.clipboard_append(file_path)
            self.update_status(f"已复制路径: {os.path.basename(file_path)}")
            self.append_log(f"已复制路径: {os.path.basename(file_path)}")
        
        path_label.bind("<Button-3>", copy_path)
        path_label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
        
        btn_frame = tk.Frame(frame, bg='white')
        btn_frame.pack(pady=5)
        
        tk.Button(btn_frame, text="打开", command=lambda p=file_path: self.open_file(p),
                 bg='#2196F3', fg='white', font=("微软雅黑", 7), 
                 padx=8, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)
        
        tk.Button(btn_frame, text="打开文件夹", 
                 command=lambda p=file_path: self.open_folder(p),
                 bg='#FF9800', fg='white', font=("微软雅黑", 7), 
                 padx=8, pady=2, cursor="hand2", width=8).pack(side=tk.LEFT, padx=2)
        
        self.thumb_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    
    def _add_image_thumbnail(self, frame, file_path):
        try:
            img = Image.open(file_path)
            img.thumbnail((160, 160))
            photo = ImageTk.PhotoImage(img)
            
            key = f"img_{len(self.thumbnail_cache)}"
            self.thumbnail_cache[key] = photo
            
            label = tk.Label(frame, image=photo, bg='white', cursor="hand2")
            label.pack(padx=8, pady=8)
            label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
            
        except Exception as e:
            tk.Label(frame, text="[加载失败]", font=("微软雅黑", 10), 
                    bg='white', fg='red').pack(padx=8, pady=20)
    
    def _add_video_thumbnail(self, frame, file_path):
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
                    return
            except:
                pass
        
        video_label = tk.Label(frame, text="[VIDEO]", font=("微软雅黑", 20, "bold"), 
                               bg='#333', fg='white', cursor="hand2")
        video_label.pack(padx=8, pady=20)
        video_label.bind("<Double-Button-1>", lambda e, p=file_path: self.open_file(p))
        frame.configure(bg='#333')
    
    def _add_generic_thumbnail(self, frame, file_path):
        tk.Label(frame, text="[FILE]", font=("微软雅黑", 20, "bold"), 
                bg='#666', fg='white').pack(padx=8, pady=20)
        frame.configure(bg='#666')
    
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
        self.start_btn.config(state='normal')
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