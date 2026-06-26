"""验证工具：检查 delete/ 文件夹是否正确包含所有重复文件"""
import sys, os
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading, json, shutil, glob, time

from hybrid_deduplicator import HybridMediaDeduplicator
from utils import get_file_size_mb, is_image, is_video
from PIL import Image, ImageTk

try:
    from video_deduplicator import extract_video_thumbnail
    HAS_THUMBNAIL = True
except ImportError:
    HAS_THUMBNAIL = False


class CheckGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Deduplicator - 验证工具")
        self.root.geometry("1500x900")
        self.root.configure(bg='#f0f0f0')
        self.groups = []
        self.correct = []      # ✅ 正确删除: [(group, should_delete_files)]
        self.missing = []      # ❌ 漏删: [(group, should_delete_files)]
        self.extra = []        # ⚠️ 误删: [file_paths]
        self.current_selection = None   # ("correct", idx) or ("missing", idx) or ("extra", idx)
        self.thumbnail_cache = {}
        self.running = False
        self.create_widgets()

    # ═══ UI ═══

    def create_widgets(self):
        title_frame = tk.Frame(self.root, bg='#2196F3', height=40)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        tk.Label(title_frame, text="Media Deduplicator - 验证工具",
                font=("微软雅黑", 14, "bold"), bg='#2196F3', fg='white').pack(pady=8)

        # 文件夹 + JSON 选择
        sel_frame = tk.Frame(self.root, bg='#f0f0f0', pady=8)
        sel_frame.pack(fill=tk.X, padx=20)

        tk.Label(sel_frame, text="文件夹:", font=("微软雅黑", 10), bg='#f0f0f0').pack(side=tk.LEFT)
        self.folder_path = tk.StringVar()
        tk.Entry(sel_frame, textvariable=self.folder_path, width=30, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)
        tk.Button(sel_frame, text="浏览...", command=self._select_folder,
                 bg='#4CAF50', fg='white', font=("微软雅黑", 9), padx=8, pady=2,
                 cursor="hand2").pack(side=tk.LEFT, padx=5)

        tk.Label(sel_frame, text="  JSON报告:", font=("微软雅黑", 10), bg='#f0f0f0').pack(side=tk.LEFT)
        self.json_path = tk.StringVar()
        tk.Entry(sel_frame, textvariable=self.json_path, width=30, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)
        tk.Button(sel_frame, text="浏览...", command=self._select_json,
                 bg='#FF9800', fg='white', font=("微软雅黑", 9), padx=8, pady=2,
                 cursor="hand2").pack(side=tk.LEFT, padx=5)

        # 控制按钮
        ctrl_frame = tk.Frame(self.root, bg='#f0f0f0', pady=5)
        ctrl_frame.pack(fill=tk.X, padx=20)
        self.check_btn = tk.Button(ctrl_frame, text="开始验证", command=self._start_check,
                                    bg='#2196F3', fg='white', font=("微软雅黑", 10, "bold"),
                                    padx=15, pady=4, cursor="hand2")
        self.check_btn.pack(side=tk.LEFT, padx=5)

        # 日志
        self.log_text = scrolledtext.ScrolledText(ctrl_frame, width=90, height=5,
                                                   font=("Consolas", 10), state="disabled",
                                                   bg="#222", fg="#d4ff54")
        self.log_text.pack(side=tk.LEFT, padx=10)

        # 状态
        self.status_label = tk.Label(ctrl_frame, text="Ready", font=("微软雅黑", 9),
                                      bg='#f0f0f0', fg='#666')
        self.status_label.pack(side=tk.LEFT, padx=5)

        # 主内容
        main_paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg='#f0f0f0')
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧：结果树
        left_frame = tk.Frame(main_paned, bg='white', relief=tk.RAISED, bd=1)
        main_paned.add(left_frame, width=420)

        tk.Label(left_frame, text="验证结果 (点击展开)", font=("微软雅黑", 10, "bold"),
                bg='white', fg='#333').pack(pady=6)

        tree_frame = tk.Frame(left_frame, bg='white')
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar = tk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(tree_frame, yscrollcommand=scrollbar.set,
                                  show='tree', selectmode='browse')
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        scrollbar.config(command=self.tree.yview)

        # 右侧：缩略图
        right_frame = tk.Frame(main_paned, bg='#e0e0e0', relief=tk.RAISED, bd=1)
        main_paned.add(right_frame, width=900)

        canvas_frame = tk.Frame(right_frame, bg='#e0e0e0')
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        v_scroll = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(canvas_frame, bg='#e0e0e0', highlightthickness=0,
                                 yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        v_scroll.config(command=self.canvas.yview)
        h_scroll.config(command=self.canvas.xview)

        self.thumb_frame = tk.Frame(self.canvas, bg='#e0e0e0')
        self.canvas.create_window((0, 0), window=self.thumb_frame, anchor="nw")
        self.thumb_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))

        def on_mwheel(event):
            x = self.canvas.winfo_rootx()
            y = self.canvas.winfo_rooty()
            if x <= event.x_root <= x + self.canvas.winfo_width() and \
               y <= event.y_root <= y + self.canvas.winfo_height():
                self.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        self.root.bind("<MouseWheel>", on_mwheel)

        # 底部操作按钮
        bottom_frame = tk.Frame(self.root, bg='#f0f0f0', pady=5)
        bottom_frame.pack(fill=tk.X, padx=20)
        tk.Button(bottom_frame, text="漏删 → 移入delete", command=self._batch_missing_to_delete,
                 bg='#f44336', fg='white', font=("微软雅黑", 9),
                 padx=10, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=5)
        tk.Button(bottom_frame, text="误删 → 恢复", command=self._batch_extra_restore,
                 bg='#FF9800', fg='white', font=("微软雅黑", 9),
                 padx=10, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=5)

    # ═══ 日志 ═══

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    # ═══ 选择 ═══

    def _select_folder(self):
        f = filedialog.askdirectory(title="选择要验证的文件夹")
        if f and os.path.isdir(f):
            self.folder_path.set(f)
            self._log(f"已选文件夹: {f}")

    def _select_json(self):
        f = filedialog.askopenfilename(title="选择 JSON 报告",
                                        filetypes=[("JSON files", "*.json")])
        if f:
            self.json_path.set(f)
            self._log(f"已选报告: {f}")

    # ═══ 主流程 ═══

    def _start_check(self):
        folder = self.folder_path.get()
        jpath = self.json_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择有效的文件夹")
            return
        if not jpath or not os.path.isfile(jpath):
            messagebox.showerror("错误", "请选择有效的 JSON 报告")
            return
        self.running = True
        self.check_btn.config(state='disabled')
        self.tree.delete(*self.tree.get_children())
        self._clear_thumbs()
        self._log("=" * 50)
        self._log("开始验证...")
        threading.Thread(target=self._run_check, args=(folder, jpath), daemon=True).start()

    def _run_check(self, folder, jpath):
        try:
            # 加载 JSON
            self._log("加载 JSON 报告...")
            with open(jpath, "r", encoding="utf-8") as f:
                report = json.load(f)
            loaded_groups = report.get("groups", [])
            self._log(f"已加载 {len(loaded_groups)} 组重复")

            # 重新扫描
            self._log("重新扫描文件夹...")
            dedup = HybridMediaDeduplicator(folder, max_workers=4, resume=False)
            dedup.scanner.scan()
            if not dedup.scanner.image_files and not dedup.scanner.video_files:
                self._log("没有找到文件")
                return
            self._log(f"扫描: {dedup.scanner.image_files} 图片, {dedup.scanner.video_files} 视频")

            # 运行去重
            self._log("运行去重...")
            dedup.run()

            # 从 JSON 合并 trashed 状态
            old_map = {}
            for og in loaded_groups:
                for f in og.get("trashed", {}).get("trashed", {}) if "trashed" in og else {}:
                    old_map[f] = og["trashed"][f]
                if "trashed" not in og:
                    continue
            for g in dedup.all_duplicates:
                for f in g["files"]:
                    if f in old_map:
                        g.setdefault("trashed", {})[f] = old_map[f]

            self.groups = dedup.all_duplicates
            self._log(f"发现 {len(self.groups)} 组重复")

            # 对比 delete/
            delete_dir = os.path.join(folder, "delete")
            delete_files = set()
            if os.path.isdir(delete_dir):
                for root, _, files in os.walk(delete_dir):
                    for fn in files:
                        delete_files.add(os.path.join(root, fn))

            self.correct = []
            self.missing = []
            extra_set = set(delete_files)

            for g in self.groups:
                trashed = g.get("trashed", {})
                candidates = [f for f in g["files"] if f not in trashed]
                should_delete, _keep = self._pick_to_delete(g, candidates)

                group_correct = []
                group_missing = []
                for f in should_delete:
                    tp = trashed.get(f, "")
                    if tp and tp in delete_files:
                        group_correct.append(f)
                        extra_set.discard(tp)
                    elif tp and os.path.exists(tp):
                        group_correct.append(f)
                        extra_set.discard(tp)
                    else:
                        group_missing.append(f)

                if group_correct:
                    self.correct.append((g, group_correct))
                if group_missing:
                    self.missing.append((g, group_missing))

            self.extra = sorted(extra_set)
            self._log(f"正确删除: {sum(len(c[1]) for c in self.correct)}")
            self._log(f"漏删: {sum(len(m[1]) for m in self.missing)}")
            self._log(f"误删: {len(self.extra)}")

            self.root.after(0, self._build_tree)

        except Exception as e:
            self._log(f"错误: {e}")
        finally:
            self.root.after(0, lambda: self.check_btn.config(state='normal'))

    def _pick_to_delete(self, group, candidates):
        """决定组内哪些文件应被删除（保留一个最优的）"""
        if len(candidates) <= 1:
            return [], candidates
        should_delete = []
        keep = None
        if "image" in group["type"]:
            best = max(candidates, key=lambda f: self._img_px(f))
            should_delete = [f for f in candidates if f != best]
            keep = [best]
        elif "video" in group["type"]:
            from utils import get_video_metadata
            metas = {}
            max_dur = 0
            for f in candidates:
                m = get_video_metadata(f)
                if m:
                    metas[f] = m
                    max_dur = max(max_dur, m.get("duration", 0))
            if max_dur > 0:
                same_dur = [f for f in candidates if f in metas and
                            abs(metas[f].get("duration", 0) - max_dur) <= 2]
                if same_dur:
                    best = max(same_dur, key=lambda f: metas[f].get("width", 0) * metas[f].get("height", 0))
                    should_delete = [f for f in candidates if f != best]
                    keep = [best]
        return should_delete, keep

    def _img_px(self, f):
        try:
            img = Image.open(f)
            return img.size[0] * img.size[1]
        except Exception:
            return 0

    # ═══ 结果树 ═══

    def _build_tree(self):
        self.tree.delete(*self.tree.get_children())

        total_correct = sum(len(c[1]) for c in self.correct)
        total_missing = sum(len(m[1]) for m in self.missing)
        total_extra = len(self.extra)

        if self.correct:
            c_node = self.tree.insert("", tk.END, text=f"✅ 正确删除 ({total_correct})",
                                       open=False, tags=("correct",))
            for i, (g, files) in enumerate(self.correct):
                ft = "[图片]" if "image" in g["type"] else "[视频]"
                g_node = self.tree.insert(c_node, tk.END,
                    text=f"{ft} {g.get('similarity','')} — {g.get('reason','')} — {len(g['files'])} 个文件",
                    tags=("correct_group",))
                self.tree.item(g_node, values=("correct", i))

        if self.missing:
            m_node = self.tree.insert("", tk.END, text=f"❌ 漏删 ({total_missing})",
                                       open=False, tags=("missing",))
            for i, (g, files) in enumerate(self.missing):
                ft = "[图片]" if "image" in g["type"] else "[视频]"
                g_node = self.tree.insert(m_node, tk.END,
                    text=f"{ft} {g.get('similarity','')} — {g.get('reason','')} — {len(g['files'])} 个文件",
                    tags=("missing_group",))
                self.tree.item(g_node, values=("missing", i))

        if self.extra:
            e_node = self.tree.insert("", tk.END, text=f"⚠️ 误删 ({total_extra})",
                                       open=False, tags=("extra",))
            for i, f in enumerate(self.extra):
                item = self.tree.insert(e_node, tk.END, text=os.path.basename(f),
                                         tags=("extra_file",))
                self.tree.item(item, values=("extra", i))

        self.tree.tag_configure("correct", foreground="#2e7d32", font=("微软雅黑", 10, "bold"))
        self.tree.tag_configure("missing", foreground="#c62828", font=("微软雅黑", 10, "bold"))
        self.tree.tag_configure("extra", foreground="#e65100", font=("微软雅黑", 10, "bold"))

    # ═══ 树选择 ═══

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        vals = self.tree.item(item, "values")
        parent = self.tree.parent(item)
        parent_text = self.tree.item(parent, "text") if parent else self.tree.item(item, "text")

        self._clear_thumbs()
        self.thumbnail_cache = {}

        if vals and len(vals) == 2:
            cat, idx = vals[0], int(vals[1])
            if cat == "correct":
                g, files = self.correct[idx]
                self._show_group_cards(g, files, is_missing=False)
            elif cat == "missing":
                g, files = self.missing[idx]
                self._show_group_cards(g, files, is_missing=True)
            elif cat == "extra":
                f = self.extra[idx]
                self.add_thumbnail(f, idx, {}, highlight=True, is_missing=False)

    # ═══ 缩略图卡片 — 复用主 GUI 逻辑 ═══

    def _show_group_cards(self, group, highlight_files, is_missing):
        trashed = group.get("trashed", {})
        for idx, f in enumerate(group["files"]):
            is_highlight = f in highlight_files
            self.add_thumbnail(f, idx, trashed, is_highlight, is_missing, group)

    def _clear_thumbs(self):
        for w in self.thumb_frame.winfo_children():
            w.destroy()
        self.thumbnail_cache = {}
        self.thumb_frame.update_idletasks()

    def add_thumbnail(self, file_path, idx, trashed=None, highlight=False, is_missing=False, group=None):
        trashed_path = trashed.get(file_path) if trashed else None
        is_trashed = trashed_path is not None
        display_path = trashed_path if is_trashed else file_path
        card_bg = '#ffcdd2' if highlight and is_missing else ('#ffcdd2' if is_trashed else 'white')

        frame = tk.Frame(self.thumb_frame, bg=card_bg, relief=tk.RAISED, bd=2)
        frame.pack(side=tk.LEFT, padx=8, pady=8, fill=tk.Y)

        # thumbnail
        info = None
        if is_image(file_path):
            info = self._add_image_thumbnail(frame, display_path)
        elif is_video(file_path):
            info = self._add_video_thumbnail(frame, display_path)
        else:
            info = self._add_generic_thumbnail(frame, display_path)

        # filename
        tk.Label(frame, text=os.path.basename(display_path), font=("微软雅黑", 8, "bold"),
                bg=card_bg, fg='#333', wraplength=200).pack(pady=(5, 0))

        # size
        tk.Label(frame, text=f"{get_file_size_mb(display_path):.1f} MB",
                font=("微软雅黑", 8), bg=card_bg, fg='#666').pack()

        # info
        if info and isinstance(info, tuple):
            parts = []
            if len(info) == 3:
                parts.extend([f"{info[0]}×{info[1]}", info[2]])
            elif len(info) == 4:
                parts.append(f"{info[0]}×{info[1]}")
                if info[2]:
                    parts.append(f"{info[2]:.0f}fps")
                if info[3]:
                    m, s = divmod(int(info[3]), 60)
                    parts.append(f"{m}:{s:02d}")
            tk.Label(frame, text="  ".join(parts), font=("微软雅黑", 7), bg=card_bg, fg='#555').pack()

        # highlight label
        if highlight:
            label_text = "❌ 漏删" if is_missing else "⚠️ 误删"
            label_color = "#c62828" if is_missing else "#e65100"
            tk.Label(frame, text=label_text, font=("微软雅黑", 7, "bold"),
                    bg=card_bg, fg=label_color).pack()

        # path
        path_label = tk.Label(frame, text=display_path, font=("微软雅黑", 6),
                              bg='#f0f0f0', fg='blue', cursor="hand2",
                              wraplength=200, justify=tk.LEFT)
        path_label.pack(pady=(3, 0), padx=5, fill=tk.X)
        path_label.bind("<Button-3>", lambda e, p=display_path: self._copy_path(p))
        path_label.bind("<Double-Button-1>", lambda e, p=display_path: self._open_file(p))

        # buttons
        btn_frame = tk.Frame(frame, bg=card_bg)
        btn_frame.pack(pady=5)

        tk.Button(btn_frame, text="打开", command=lambda p=display_path: self._open_file(p),
                 bg='#2196F3', fg='white', font=("微软雅黑", 7),
                 padx=8, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="文件夹", command=lambda p=display_path: self._open_folder(p),
                 bg='#FF9800', fg='white', font=("微软雅黑", 7),
                 padx=8, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)

        if highlight and is_missing:
            tk.Button(btn_frame, text="标记删除",
                      command=lambda fp=file_path, fr=frame: self._mark_and_move(fp, fr, group),
                      bg='#f44336', fg='white', font=("微软雅黑", 7),
                      padx=6, pady=2, cursor="hand2", width=8).pack(side=tk.LEFT, padx=2)
        elif highlight and not is_missing:
            tk.Button(btn_frame, text="恢复",
                      command=lambda fp=file_path, fr=frame: self._restore_extra(fp, fr),
                      bg='#4CAF50', fg='white', font=("微软雅黑", 7),
                      padx=6, pady=2, cursor="hand2", width=6).pack(side=tk.LEFT, padx=2)

        self.thumb_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _mark_and_move(self, file_path, old_frame, group):
        """和主 GUI 一样的标记删除 + 更新 JSON"""
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
            messagebox.showerror("失败", str(e))
            return
        # 更新 group
        group.setdefault("trashed", {})[file_path] = target
        self._save_json()
        old_frame.destroy()
        self._log(f"已标记: {os.path.basename(file_path)}")

    def _restore_extra(self, file_path, old_frame):
        """恢复误删文件：去(delete)，移回原位"""
        # 推算原始路径
        dname = os.path.basename(file_path)
        if "(delete)" in dname:
            orig_name = dname.replace("(delete)", "")
            # 去序号
            import re
            orig_name = re.sub(r'\(\d+\)\(delete\)', '', orig_name)
            if orig_name.endswith(")"):
                orig_name = orig_name.replace("(delete)", "")
        else:
            orig_name = dname

        # 在同目录下找同名且不含(delete)的文件作为原始路径参考
        scan_root = self.folder_path.get()
        restored = os.path.join(os.path.dirname(file_path), orig_name)
        if os.path.dirname(restored).startswith(os.path.join(scan_root, "delete")):
            # 推回原位
            rel = os.path.relpath(os.path.dirname(file_path), os.path.join(scan_root, "delete"))
            restored = os.path.join(scan_root, rel, orig_name)

        counter = 1
        while os.path.exists(restored):
            d, n = os.path.split(restored)
            nm, ex = os.path.splitext(n)
            restored = os.path.join(d, f"{nm} (恢复{counter}){ex}")
            counter += 1

        try:
            os.makedirs(os.path.dirname(restored), exist_ok=True)
            shutil.move(file_path, restored)
        except Exception as e:
            messagebox.showerror("失败", str(e))
            return
        self._log(f"已恢复: {os.path.basename(file_path)}")
        # 从 extra 列表移除并更新树
        self.extra.remove(file_path)
        old_frame.destroy()
        self._build_tree()

    # ═══ 批量操作 ═══

    def _batch_missing_to_delete(self):
        if not self.missing:
            messagebox.showinfo("提示", "没有漏删文件")
            return
        count = 0
        for g, files in self.missing:
            for f in files:
                try:
                    self._mark_and_move_silent(f, g)
                    count += 1
                except Exception:
                    pass
        self._save_json()
        self._log(f"批量移入 {count} 个文件")
        # 重新运行验证
        self._start_check()

    def _mark_and_move_silent(self, file_path, group):
        """无弹窗版标记删除"""
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
        shutil.move(file_path, target)
        group.setdefault("trashed", {})[file_path] = target

    def _batch_extra_restore(self):
        if not self.extra:
            messagebox.showinfo("提示", "没有误删文件")
            return
        count = 0
        for f in list(self.extra):
            try:
                self._restore_extra_silent(f)
                count += 1
            except Exception:
                pass
        self._log(f"批量恢复 {count} 个文件")
        self._build_tree()

    def _restore_extra_silent(self, file_path):
        import re
        dname = os.path.basename(file_path)
        if "(delete)" in dname:
            orig_name = dname.replace("(delete)", "")
            orig_name = re.sub(r'\(\d+\)\(delete\)', '', orig_name)
        else:
            orig_name = dname
        scan_root = self.folder_path.get()
        restored = os.path.join(os.path.dirname(file_path), orig_name)
        if os.path.dirname(restored).startswith(os.path.join(scan_root, "delete")):
            rel = os.path.relpath(os.path.dirname(file_path), os.path.join(scan_root, "delete"))
            restored = os.path.join(scan_root, rel, orig_name)
        counter = 1
        while os.path.exists(restored):
            d, n = os.path.split(restored)
            nm, ex = os.path.splitext(n)
            restored = os.path.join(d, f"{nm} (恢复{counter}){ex}")
            counter += 1
        os.makedirs(os.path.dirname(restored), exist_ok=True)
        shutil.move(file_path, restored)
        self.extra.remove(file_path)

    # ═══ JSON 同步 ═══

    def _save_json(self):
        jpath = self.json_path.get()
        if not jpath:
            return
        try:
            data = {"summary": {}, "groups": self.groups}
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ═══ 缩略图方法（从主 GUI 复制）═══

    def _add_image_thumbnail(self, frame, file_path):
        try:
            img = Image.open(file_path)
            w, h = img.size
            fmt = img.format or "未知"
            img.thumbnail((160, 160))
            photo = ImageTk.PhotoImage(img)
            key = f"img_{len(self.thumbnail_cache)}"
            self.thumbnail_cache[key] = photo
            tk.Label(frame, image=photo, bg='white', cursor="hand2").pack(padx=8, pady=8)
            return w, h, fmt
        except Exception:
            tk.Label(frame, text="[加载失败]", font=("微软雅黑", 10), bg='white', fg='red').pack(padx=8, pady=20)
            return None

    def _add_video_thumbnail(self, frame, file_path):
        from utils import get_video_metadata
        meta = get_video_metadata(file_path)
        if HAS_THUMBNAIL:
            try:
                img, _ = extract_video_thumbnail(file_path)
                if img:
                    photo = ImageTk.PhotoImage(img)
                    key = f"vid_{len(self.thumbnail_cache)}"
                    self.thumbnail_cache[key] = photo
                    tk.Label(frame, image=photo, bg='#333', cursor="hand2").pack(padx=8, pady=8)
                    if meta:
                        return meta["width"], meta["height"], meta["fps"], meta["duration"]
                    return None
            except Exception:
                pass
        tk.Label(frame, text="[VIDEO]", font=("微软雅黑", 20, "bold"),
                bg='#333', fg='white', cursor="hand2").pack(padx=8, pady=20)
        frame.configure(bg='#333')
        if meta:
            return meta["width"], meta["height"], meta["fps"], meta["duration"]
        return None

    def _add_generic_thumbnail(self, frame, file_path):
        tk.Label(frame, text="[FILE]", font=("微软雅黑", 20, "bold"),
                bg='#666', fg='white').pack(padx=8, pady=20)
        frame.configure(bg='#666')
        return None

    # ═══ 文件操作 ═══

    def _copy_path(self, path):
        self.root.clipboard_clear()
        self.root.clipboard_append(path)

    def _open_file(self, file_path):
        if not os.path.exists(file_path):
            return
        try:
            os.startfile(os.path.normpath(file_path))
        except Exception:
            subprocess.run(['cmd', '/c', 'start', '', os.path.normpath(file_path)], shell=False)

    def _open_folder(self, file_path):
        if not os.path.exists(file_path):
            return
        try:
            subprocess.run(['explorer', '/select,', os.path.normpath(file_path)])
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = CheckGUI(root)
    root.mainloop()
