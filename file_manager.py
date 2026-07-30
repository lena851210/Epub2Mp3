# -*- coding: utf-8 -*-
"""
文件列表管理与目录监测模块
"""

import os
import re
import tkinter as tk
import platform
import subprocess
import threading

from audio_processor import find_existing_outputs_for_txt


class FileManagerMixin:
    """文件列表、目录监测、选择状态相关方法"""

    def _get_dir_snapshot(self):
        """获取当前 TXT 目录快照，用于检测外部变化"""
        directory = self.txt_dir.get().strip()
        if not directory:
            return ("", False, ())

        if not os.path.isdir(directory):
            return (directory, False, ())

        try:
            txt_files = sorted([f for f in os.listdir(directory) if f.lower().endswith(".txt")])
        except Exception:
            txt_files = []

        return (directory, True, tuple(txt_files))

    def _refresh_dir_snapshot(self):
        """刷新目录快照"""
        self.last_dir_snapshot = self._get_dir_snapshot()

    def _start_dir_watch(self):
        """启动目录轮询监测"""
        self._refresh_dir_snapshot()
        self._schedule_dir_watch()

    def _schedule_dir_watch(self):
        """定时检查目录变化"""
        if self.dir_watch_job is not None:
            try:
                self.root.after_cancel(self.dir_watch_job)
            except Exception:
                pass

        self.dir_watch_job = self.root.after(1500, self._poll_dir_changes)

    def _poll_dir_changes(self):
        """轮询检测当前 TXT 目录是否变化"""
        try:
            current_snapshot = self._get_dir_snapshot()
            if current_snapshot != self.last_dir_snapshot:
                self.last_dir_snapshot = current_snapshot
                self._handle_dir_change(current_snapshot)
        finally:
            self._schedule_dir_watch()

    def _handle_dir_change(self, snapshot):
        """处理目录变化"""
        directory, exists, txt_files = snapshot

        if not directory:
            self.update_action_buttons_state()
            return

        if not exists:
            self._clear_file_list_ui("当前TXT目录不存在或已被删除")
            self.set_status("检测到当前TXT目录已不存在")
            return

        if exists and not txt_files:
            self.load_file_list(directory)
            self.set_status("检测到当前目录中的TXT文件已发生变化")
            return

        self.load_file_list(directory)
        self.set_status("检测到TXT目录内容已更新")

    def _clear_file_list_ui(self, info_text: str):
        """清空文件列表 UI 状态"""
        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)

        self.selection_states.clear()
        self.file_chars.clear()
        self.progress_vars.clear()

        for pb in self.tree_progress.values():
            try:
                pb.destroy()
            except Exception:
                pass
        self.tree_progress.clear()

        self.files_info_var.set(info_text)
        self.update_action_buttons_state()

    def refresh_txt_dir_state(self) -> bool:
        """
        重新检查当前 TXT 目录是否仍有效。
        返回 True 表示目录有效且至少有一个 TXT 文件；
        返回 False 表示目录无效或没有 TXT，并同步刷新界面状态。
        """
        directory = self.txt_dir.get().strip()

        if not directory or not os.path.isdir(directory):
            self._clear_file_list_ui("当前TXT目录不存在或已被删除")
            self._refresh_dir_snapshot()
            return False

        txt_files = [f for f in os.listdir(directory) if f.lower().endswith(".txt")]
        if not txt_files:
            self.load_file_list(directory)
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()
            return False

        self._refresh_dir_snapshot()
        return True

    def update_action_buttons_state(self):
        """根据当前 TXT 列表状态，更新操作按钮启用/禁用状态"""
        try:
            directory = self.txt_dir.get().strip()
            has_txt_dir = bool(directory and os.path.isdir(directory))
            has_files_in_tree = bool(self.files_tree.get_children())

            has_real_txt_files = False
            if has_txt_dir:
                try:
                    has_real_txt_files = any(
                        f.lower().endswith(".txt") for f in os.listdir(directory)
                    )
                except Exception:
                    has_real_txt_files = False

            can_start = has_txt_dir and has_files_in_tree and has_real_txt_files

            if hasattr(self, "start_btn"):
                self._set_primary_button_state(self.start_btn, can_start)

            if hasattr(self, "import_epub_btn"):
                self._set_primary_button_state(self.import_epub_btn, True)

        except Exception:
            pass

    def load_file_list(self, directory: str):
        """快速加载 TXT 文件列表；音频存在性检查异步进行，避免界面卡顿"""
        self._load_token = getattr(self, "_load_token", 0) + 1
        load_token = self._load_token

        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)

        self.selection_states.clear()
        self.file_chars.clear()
        self.progress_vars.clear()

        for pb in self.tree_progress.values():
            try:
                pb.destroy()
            except Exception:
                pass
        self.tree_progress.clear()

        if not os.path.isdir(directory):
            self.files_info_var.set("当前目录无效")
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()
            return

        all_txt = [f for f in os.listdir(directory) if f.lower().endswith(".txt")]

        def sort_key(fname: str):
            stem = os.path.splitext(fname)[0].strip()
            m = re.match(r"^\s*(\d{1,4})(?:-(\d{1,4}))?[\s_-]*(.*)$", stem)
            if m:
                chap = int(m.group(1))
                part = int(m.group(2)) if m.group(2) else 0
                title = (m.group(3) or "").strip()
                return (chap, part, title, fname)
            return (10**9, 0, stem, fname)

        files = sorted(all_txt, key=sort_key)
        if not files:
            self.files_info_var.set("该目录下没有 TXT 文件")
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()
            return

        # 重置“正在异步检查”状态
        self._audio_check_running = True
        self._audio_check_total = len(files)
        self._audio_check_done = 0

        # 第一步：先快速显示列表，不做耗时音频检查
        for idx, fname in enumerate(files):
            full_path = os.path.join(directory, fname)

            try:
                size_kb = os.path.getsize(full_path) / 1024.0
                size_str = f"{size_kb:.1f}"
            except Exception:
                size_str = "-"

            text = self.read_text_file(full_path)
            chars = self.duration_estimator.count_chars(text) if text is not None else 0

            self.file_chars[fname] = chars
            est_str = self.estimate_duration_str(chars)

            iid = fname

            # 初始状态：未决定是否勾选
            self.selection_states[iid] = False

            check_mark = ""
            tag = "oddrow" if idx % 2 else "evenrow"

            self.files_tree.insert(
                "", "end", iid=iid,
                values=(check_mark, fname, size_str, str(chars), est_str, "检查中...", ""),
                tags=(tag,)
            )

        # 顶部先显示“检查中”
        self.files_info_var.set(f"正在检查音频状态 0/{len(files)} ...")
        self.update_action_buttons_state()
        self._refresh_dir_snapshot()

        # 先把界面渲染出来，再异步扫描
        self.root.after(50, lambda: self._start_async_audio_status_check(directory, files, load_token))

    def _start_async_audio_status_check(self, directory: str, files, load_token: int):
        """启动后台线程，异步检查哪些 TXT 已有正式音频"""
        def worker():
            try:
                out_dir = ""
                try:
                    out_dir = self.get_audio_output_dir()
                except Exception:
                    out_dir = ""

                total = len(files)

                for idx, fname in enumerate(files, 1):
                    if load_token != getattr(self, "_load_token", None):
                        return

                    existing_outputs = []
                    status_text = "待处理"
                    is_selected = True

                    try:
                        existing_outputs = find_existing_outputs_for_txt(out_dir, fname) if out_dir else []
                        if existing_outputs:
                            if len(existing_outputs) == 1:
                                status_text = "✅ 已存在"
                            else:
                                status_text = f"已存在({len(existing_outputs)}段)"
                            is_selected = False
                        else:
                            status_text = "待处理"
                            is_selected = True
                    except Exception:
                        status_text = "待处理"
                        is_selected = True

                    self.root.after(
                        0,
                        lambda f=fname, st=status_text, sel=is_selected, i=idx, t=total, token=load_token:
                            self._apply_audio_check_result(f, st, sel, i, t, token)
                    )

                self.root.after(
                    0,
                    lambda token=load_token: self._finish_async_audio_status_check(token)
                )

            except Exception:
                self.root.after(
                    0,
                    lambda token=load_token: self._finish_async_audio_status_check(token)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_audio_check_result(self, iid: str, status_text: str, is_selected: bool, idx: int, total: int, token: int):
        """把后台检查结果应用到界面"""
        if token != getattr(self, "_load_token", None):
            return

        if not self.files_tree.exists(iid):
            return

        self.selection_states[iid] = is_selected
        check_mark = "✓" if is_selected else ""

        current_values = self.files_tree.item(iid, "values")
        if not current_values or len(current_values) < 7:
            return

        new_values = (
            check_mark,
            current_values[1],
            current_values[2],
            current_values[3],
            current_values[4],
            status_text,
            current_values[6]
        )
        self.files_tree.item(iid, values=new_values)

        self._audio_check_done = idx
        self.files_info_var.set(f"正在检查音频状态 {idx}/{total} ...")

    def _finish_async_audio_status_check(self, token: int):
        """结束异步音频状态检查"""
        if token != getattr(self, "_load_token", None):
            return

        self._audio_check_running = False
        self.update_selection_info()
        self.update_action_buttons_state()
        self.set_status("TXT 列表已加载")

    def _on_tree_click(self, event):
        """点击表格：只允许点击第一列切换勾选，其他列仅正常选中行"""
        row = self.files_tree.identify_row(event.y)
        col = self.files_tree.identify_column(event.x)
        region = self.files_tree.identify_region(event.x, event.y)

        if not row or not col or region != "cell":
            return

        if col == "#1":
            self._toggle_selection(row)
            return "break"

        return

    def _toggle_selection(self, iid: str):
        """切换单���文件的选择状态"""
        if not self.files_tree.exists(iid):
            return

        self.selection_states[iid] = not self.selection_states.get(iid, False)
        check_mark = "✓" if self.selection_states[iid] else ""
        current_values = self.files_tree.item(iid, "values")
        new_values = (check_mark,) + current_values[1:]
        self.files_tree.item(iid, values=new_values)
        self.update_selection_info()

    def refresh_tree_overlays(self):
        """刷新进度条位置"""
        tree_h = self.files_tree.winfo_height()

        for iid in self.files_tree.get_children(""):
            bbox_prog = self.files_tree.bbox(iid, column="#7")
            if bbox_prog:
                x, y, w, h = bbox_prog
                if not (y + h < 0 or y > tree_h):
                    var = self.progress_vars.get(iid)
                    if var is None:
                        var = tk.DoubleVar(value=0.0)
                        self.progress_vars[iid] = var
                    pb = self.tree_progress.get(iid)
                    if pb is None:
                        from tkinter import ttk
                        pb = ttk.Progressbar(
                            self.files_tree,
                            orient="horizontal",
                            mode="determinate",
                            maximum=100.0,
                            variable=var
                        )
                        self.tree_progress[iid] = pb
                    pb.place(x=x + 6, y=y + 6, width=max(40, w - 12), height=h - 12)
                else:
                    pb = self.tree_progress.get(iid)
                    if pb:
                        pb.place_forget()

    def update_selection_info(self):
        """更新选择信息"""
        total = len(self.selection_states)
        selected = sum(1 for v in self.selection_states.values() if v)

        # 检查过程中显示“正在检查...”
        if getattr(self, "_audio_check_running", False):
            done = getattr(self, "_audio_check_done", 0)
            total_check = getattr(self, "_audio_check_total", total)
            self.files_info_var.set(f"正在检查音频状态 {done}/{total_check} ...")
        else:
            self.files_info_var.set(f"已选择 {selected}/{total} 个文件")

    def select_all_files(self):
        """全选"""
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = True
            current_values = self.files_tree.item(iid, "values")
            self.files_tree.item(iid, values=("✓",) + current_values[1:])
        self.update_selection_info()

    def unselect_all_files(self):
        """全不选"""
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = False
            current_values = self.files_tree.item(iid, "values")
            self.files_tree.item(iid, values=("",) + current_values[1:])
        self.update_selection_info()

    def invert_selection(self):
        """反选"""
        for iid in self.files_tree.get_children():
            cur = self.selection_states.get(iid, True)
            self.selection_states[iid] = not cur
            check_mark = "✓" if self.selection_states[iid] else ""
            current_values = self.files_tree.item(iid, "values")
            self.files_tree.item(iid, values=(check_mark,) + current_values[1:])
        self.update_selection_info()

    def on_tree_double_click(self, event):
        """双击打开文件预览"""
        region = self.files_tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        col = self.files_tree.identify_column(event.x)
        row = self.files_tree.identify_row(event.y)
        if not row:
            return

        if col == "#2":
            path = os.path.join(self.txt_dir.get(), row)
            self.open_text_preview(path)

    def open_text_preview(self, path: str):
        """打开文本预览"""
        try:
            sysname = platform.system()
            if sysname == "Darwin":
                subprocess.run(["open", path], check=False)
            elif sysname == "Windows":
                os.startfile(path)
            else:
                subprocess.run(["xdg-open", path], check=False)
            self.set_status(f"已打开预览: {os.path.basename(path)}")
        except Exception as e:
            self.set_status(f"预览失败: {e}")