# -*- coding: utf-8 -*-
"""
音频生成与转换流程模块
"""

import os
import time
import threading
from datetime import datetime
from typing import List, Tuple, Optional
from tkinter import messagebox

from audio_processor import (
    _process_audio_chunk,
    build_output_path,
    find_existing_outputs_for_txt,
    get_original_chapter_number,
)
from epub_processor import find_saved_epub_cover, find_saved_epub_metadata
from models import TTSRequestStopped, TTSResult


def classify_task_status(status_text: str) -> Optional[str]:
    """把界面状态归一成可统计的任务结果。"""
    status = (status_text or "").strip().lstrip("✅").strip()

    if status.startswith("已完成"):
        return "success"
    if status.startswith("已存在") or status.startswith("跳过"):
        return "skipped"
    if status.startswith("失败") or "合成失败" in status:
        return "failed"
    if status.startswith("已中断"):
        return "stopped"
    return None


def summarize_task(task_files, task_statuses, task_progress):
    """计算总体进度和成功/跳过/失败/中断数量。"""
    files = list(dict.fromkeys(task_files or []))
    total = len(files)
    counts = {
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "stopped": 0,
    }

    weighted_progress = 0.0
    for file_name in files:
        category = classify_task_status(task_statuses.get(file_name, ""))
        if category:
            counts[category] += 1

        file_progress = max(
            0.0,
            min(100.0, float(task_progress.get(file_name, 0.0)))
        )
        if category:
            file_progress = max(file_progress, 100.0)
        weighted_progress += file_progress

    handled = sum(counts.values())
    percent = int(round(weighted_progress / total)) if total else 0
    return {
        "total": total,
        "handled": handled,
        "pending": max(0, total - handled),
        "percent": percent,
        **counts,
    }


class GenerationMixin:
    """音频生成相关方法"""

    @staticmethod
    def format_elapsed_time(seconds: float) -> str:
        """把任务耗时格式化为固定宽度，长任务超过 24 小时也能正确显示。"""
        total_seconds = max(0, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def get_elapsed_seconds(self) -> int:
        """取得当前或最近一次任务的总耗时。"""
        started_at = getattr(self, "task_started_at", None)
        if started_at is None:
            return int(getattr(self, "last_elapsed_seconds", 0))
        return max(0, int(time.monotonic() - started_at))

    def _refresh_elapsed_time(self):
        """每秒刷新一次总耗时；停止请求后继续计时，直到任务真正退出。"""
        elapsed_seconds = self.get_elapsed_seconds()
        self.last_elapsed_seconds = elapsed_seconds
        self.elapsed_time_var.set(f"总耗时：{self.format_elapsed_time(elapsed_seconds)}")
        self.elapsed_timer_job = None
        if self.is_generating:
            self.elapsed_timer_job = self.root.after(1000, self._refresh_elapsed_time)

    def start_elapsed_timer(self):
        """开始一次新的转换计时。"""
        if getattr(self, "elapsed_timer_job", None) is not None:
            try:
                self.root.after_cancel(self.elapsed_timer_job)
            except Exception:
                pass
        self.task_started_at = time.monotonic()
        self.last_elapsed_seconds = 0
        self.elapsed_time_var.set("总耗时：00:00:00")
        self.elapsed_timer_job = self.root.after(1000, self._refresh_elapsed_time)

    def stop_elapsed_timer(self):
        """冻结最终耗时并取消界面定时任务。"""
        if getattr(self, "elapsed_timer_job", None) is not None:
            try:
                self.root.after_cancel(self.elapsed_timer_job)
            except Exception:
                pass
        self.elapsed_timer_job = None
        self.last_elapsed_seconds = self.get_elapsed_seconds()
        self.task_started_at = None
        self.elapsed_time_var.set(
            f"总耗时：{self.format_elapsed_time(self.last_elapsed_seconds)}"
        )

    def estimate_duration(self, text: str) -> float:
        """估算时长（分钟）"""
        wpm = max(1, self.wpm_var.get())
        clean_text = text.replace(" ", "").replace("\n", "").replace("\t", "")
        char_count = len(clean_text)
        return char_count / wpm

    def split_long_text(self, text: str, target_duration: int, file_name: str) -> List[Tuple[str, List[str]]]:
        """将长文本分割为多个接近目标时长的部分"""
        wpm = max(1, self.wpm_var.get())
        target_chars = target_duration * wpm

        parts = []
        paragraphs = text.split("\n\n")
        current = ""

        for para in paragraphs:
            if not para.strip():
                continue

            test = current + "\n\n" + para if current else para
            clean_len = len(test.replace(" ", "").replace("\n", "").replace("\t", ""))

            if clean_len <= target_chars:
                current = test
            else:
                if current:
                    parts.append((current, [file_name]))

                para_clean_len = len(para.replace(" ", "").replace("\n", "").replace("\t", ""))
                if para_clean_len > target_chars:
                    sub_parts = self._split_paragraph(para, target_chars)
                    parts.extend([(p, [file_name]) for p in sub_parts])
                    current = ""
                else:
                    current = para

        if current:
            parts.append((current, [file_name]))

        return parts if parts else [(text, [file_name])]

    def _split_paragraph(self, paragraph: str, target_chars: int) -> List[str]:
        """递归分割超长段落"""
        para_clean_len = len(paragraph.replace(" ", "").replace("\n", "").replace("\t", ""))

        if para_clean_len <= target_chars:
            return [paragraph]

        sentences = paragraph.replace("！", "！\n").replace("？", "？\n").replace("。", "。\n").split("\n")

        parts = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            test = current + sentence if not current else current + "\n" + sentence
            test_len = len(test.replace(" ", "").replace("\n", "").replace("\t", ""))

            if test_len <= target_chars:
                current = test
            else:
                if current:
                    parts.append(current)

                sentence_len = len(sentence.replace(" ", "").replace("\n", "").replace("\t", ""))
                if sentence_len > target_chars:
                    sub_parts = self._split_by_chars(sentence, target_chars)
                    parts.extend(sub_parts)
                    current = ""
                else:
                    current = sentence

        if current:
            parts.append(current)

        return parts

    def _split_by_chars(self, text: str, target_chars: int) -> List[str]:
        """按字符数强制分割"""
        if len(text) <= target_chars:
            return [text]

        parts = []
        for i in range(0, len(text), target_chars):
            parts.append(text[i:i + target_chars])
        return parts

    def get_selected_files(self) -> List[str]:
        """获取已选择的文件列表"""
        directory = self.txt_dir.get()
        if not directory or not os.path.isdir(directory):
            return []
        return [iid for iid in self.files_tree.get_children() if self.selection_states.get(iid, False)]

    def set_file_progress(self, iid: str, percent: float):
        """设置文件进度"""
        progress = max(0.0, min(100.0, float(percent)))

        def apply():
            if iid in getattr(self, "task_files", []):
                self.task_progress[iid] = progress
                self.update_overall_progress()

        self.root.after(0, apply)

    def set_error(self, iid: str, exc: Exception):
        """设置错误信息"""
        self.error_detail[iid] = str(exc)

    def calculate_segment_timeout(self, text: str) -> float:
        """按实际字数与语速计算单段总超时，限制在 30~75 秒。"""
        char_count = len((text or "").replace(" ", "").replace("\n", "").replace("\t", ""))
        chars_per_minute = max(1, int(self.wpm_var.get()))
        estimated_audio_seconds = char_count / chars_per_minute * 60.0
        return min(75.0, max(30.0, estimated_audio_seconds * 0.45 + 14.0))

    def log_tts_attempt(
        self,
        *,
        iid: Optional[str],
        attempt: int,
        max_attempts: int,
        elapsed_seconds: float,
        timeout_seconds: float,
        result: TTSResult,
        error: Optional[Exception] = None,
    ):
        """记录 segment 请求耗时，供后续校准 timeout；不增加复杂 UI。"""
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_runtime.log")
        safe_name = os.path.basename(iid or "unknown")
        error_text = str(error or "").replace("\n", " ").strip()
        line = (
            f"{datetime.now().isoformat(timespec='seconds')}\t"
            f"file={safe_name}\tattempt={attempt}/{max_attempts}\t"
            f"elapsed={elapsed_seconds:.2f}s\ttimeout={timeout_seconds:.0f}s\t"
            f"result={result.value}"
        )
        if error_text:
            line += f"\terror={error_text[:300]}"
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
        except Exception:
            pass

    @staticmethod
    def _remove_incomplete_segment(output_file: str):
        """删除当前未完成临时文件；正式成功文件不会传入这里。"""
        try:
            if os.path.exists(output_file):
                os.remove(output_file)
        except Exception:
            pass

    @staticmethod
    def _remove_incomplete_split_outputs(paths):
        """回收本轮未完整章节刚生成的正式分段，不触碰运行前已有文件。"""
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass

    def tts_with_retry(self, text: str, output_file: str, iid_for_error: Optional[str] = None, max_retries: int = 3) -> TTSResult:
        """TTS 转换：首次尝试失败后最多重试两次。"""
        last_exc = None
        parent = os.path.dirname(output_file) or "."
        os.makedirs(parent, exist_ok=True)
        timeout_seconds = self.calculate_segment_timeout(text)

        for n in range(1, max_retries + 1):
            if self.stop_flag or self.tts_cancel_event.is_set():
                self._remove_incomplete_segment(output_file)
                return TTSResult.STOPPED

            if n > 1 and iid_for_error:
                self.set_file_status(
                    iid_for_error,
                    f"正在重试 · {n - 1}/{max_retries - 1}",
                    spinning=True,
                )

            started_at = time.monotonic()
            try:
                self._remove_incomplete_segment(output_file)

                self.edge.text_to_speech(
                    text=text,
                    voice=self.voice_var.get(),
                    speed=self.speed_var.get(),
                    pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(),
                    output_file=output_file,
                    total_timeout=timeout_seconds,
                    cancel_event=self.tts_cancel_event,
                )

                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    self.log_tts_attempt(
                        iid=iid_for_error,
                        attempt=n,
                        max_attempts=max_retries,
                        elapsed_seconds=time.monotonic() - started_at,
                        timeout_seconds=timeout_seconds,
                        result=TTSResult.SUCCESS,
                    )
                    return TTSResult.SUCCESS
                raise RuntimeError(f"TTS输出为空或未生成: {os.path.basename(output_file)}")

            except TTSRequestStopped as e:
                self._remove_incomplete_segment(output_file)
                self.log_tts_attempt(
                    iid=iid_for_error,
                    attempt=n,
                    max_attempts=max_retries,
                    elapsed_seconds=time.monotonic() - started_at,
                    timeout_seconds=timeout_seconds,
                    result=TTSResult.STOPPED,
                    error=e,
                )
                return TTSResult.STOPPED
            except Exception as e:
                last_exc = e
                self._remove_incomplete_segment(output_file)
                self.log_tts_attempt(
                    iid=iid_for_error,
                    attempt=n,
                    max_attempts=max_retries,
                    elapsed_seconds=time.monotonic() - started_at,
                    timeout_seconds=timeout_seconds,
                    result=TTSResult.FAILED,
                    error=e,
                )
                if self.stop_flag or self.tts_cancel_event.is_set():
                    return TTSResult.STOPPED
                if n < max_retries:
                    time.sleep(min(1.5, 0.5 * n))

        if iid_for_error:
            self.set_error(iid_for_error, last_exc)
        return TTSResult.FAILED

    def start_generation(self):
        """开始转换任务"""
        if self.is_generating:
            self.set_status("转换任务正在进行中，请勿重复启动。")
            return

        if not self.refresh_txt_dir_state():
            self.set_status("请先选择有效的TXT目录")
            self.root.after(
                0,
                lambda: messagebox.showwarning(
                    "提示",
                    "请先选择有效的 TXT 目录，且目录中至少有一个 TXT 文件。"
                )
            )
            return

        files = self.get_selected_files()
        if not files:
            self.set_status("请先在文件列表中勾选至少一个TXT文件。")
            messagebox.showwarning(
                "提示",
                "请在文件列表中勾选至少一个 TXT 文件。"
            )
            return

        self.stop_flag = False
        self.tts_cancel_event.clear()
        self.is_generating = True
        self.task_files = list(files)
        self.task_statuses = {}
        self.task_progress = {file_name: 0.0 for file_name in files}
        self.reset_overall_progress()
        self.start_elapsed_timer()

        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，若分段>1将无法合并；请先安装 ffmpeg。")

        self.update_action_buttons_state()
        self.set_status("开始转换任务...")
        self.generation_thread = threading.Thread(
            target=self._run_generation_task,
            daemon=True
        )
        self.generation_thread.start()

    def stop_generation(self):
        """停止转换任务"""
        if not self.is_generating:
            self.set_status("当前没有正在进行的转换任务。")
            return

        self.stop_flag = True
        self.tts_cancel_event.set()
        self.set_status("已请求停止；正在取消当前语音请求...")

    def _run_generation_task(self):
        """在后台运行转换，并确保按钮状态最终恢复。"""
        try:
            self.generate()
        except Exception as e:
            self.set_status(f"转换任务异常：{e}")
            self.root.after(
                0,
                lambda msg=str(e): messagebox.showerror(
                    "转换失败",
                    f"转换任务意外中断。\n\n错误信息：\n{msg}"
                )
            )
        finally:
            def finish():
                self.stop_elapsed_timer()
                self.is_generating = False
                self.generation_thread = None
                self.update_action_buttons_state()
                if getattr(self, "exit_after_stop", False):
                    self._close_app()

            try:
                self.root.after(0, finish)
            except Exception:
                pass

    def reset_overall_progress(self):
        """开始新任务时重置总体进度。"""
        total = len(getattr(self, "task_files", []))
        self.overall_progress_var.set(0.0)
        self.overall_progress_text_var.set(f"整体进度：0/{total} · 0%")

    def update_overall_progress(self, final: bool = False):
        """根据单章状态刷新整本书进度。"""
        summary = summarize_task(
            getattr(self, "task_files", []),
            getattr(self, "task_statuses", {}),
            getattr(self, "task_progress", {}),
        )
        percent = 100 if final and summary["total"] else summary["percent"]
        self.overall_progress_var.set(percent)

        if not summary["total"]:
            self.overall_progress_text_var.set("整体进度：未开始")
            return summary

        if final:
            prefix = "已停止" if self.stop_flag else "已完成"
            self.overall_progress_text_var.set(
                f"{prefix}：成功 {summary['success']} · "
                f"跳过 {summary['skipped']} · "
                f"失败 {summary['failed']} · "
                f"中断 {summary['stopped']}"
            )
        else:
            self.overall_progress_text_var.set(
                f"整体进度：{summary['handled']}/{summary['total']} · {percent}%"
            )
        return summary

    def show_task_result(self, out_dir: str):
        """显示一次清楚、可复核的任务结果。"""
        summary = self.update_overall_progress(final=True)
        stopped = self.stop_flag

        title = "转换已停止" if stopped else "转换完成"
        body = (
            f"成功：{summary['success']} 个\n"
            f"已跳过：{summary['skipped']} 个\n"
            f"失败：{summary['failed']} 个\n"
            f"已中断：{summary['stopped']} 个"
        )
        if summary["pending"]:
            body += f"\n未完成：{summary['pending']} 个"
        body += f"\n总耗时：{self.format_elapsed_time(self.get_elapsed_seconds())}"
        body += f"\n\n音频目录：\n{out_dir}"

        messagebox.showinfo(title, body)

    def generate(self):
        """生成有声书"""
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            self.root.after(0, lambda: messagebox.showerror("错误", "请选择有效的TXT目录"))
            return

        out_dir = self.get_audio_output_dir()
        if not out_dir:
            self.root.after(0, lambda: messagebox.showerror("错误", "无法确定音频输出目录"))
            return

        os.makedirs(out_dir, exist_ok=True)
        self.current_cover_path = find_saved_epub_cover(txt_dir)
        self.current_book_metadata = find_saved_epub_metadata(txt_dir)

        files = list(getattr(self, "task_files", []))
        if not files:
            self.set_status("请先在文件列表中勾选至少一个TXT文件。")
            self.root.after(0, lambda: messagebox.showwarning("提示", "请在文件列表中勾选至少一个TXT文件。"))
            return

        if not self.merge_var.get():
            self.generate_plain_files(files, txt_dir, out_dir)
        else:
            self.generate_smart_by_target(files, txt_dir, out_dir)

        if self.stop_flag:
            for f in files:
                cur = self.task_statuses.get(f, "")
                if classify_task_status(cur) is None:
                    self.set_file_status(f, "已中断", spinning=False)
            self.set_status("任务已中断。")
        else:
            self.set_status(f"所有任务处理完成。输出目录：{out_dir}")

        # 先冻结真实处理耗时，再显示结果；避免把用户阅读结果弹窗的时间算进去。
        self.root.after(0, self.stop_elapsed_timer)
        if not getattr(self, "exit_after_stop", False):
            self.root.after(0, lambda path=out_dir: self.show_task_result(path))

    def generate_plain_files(self, files: List[str], txt_dir: str, out_dir: str):
        """普通模式：每个 TXT 直接输出一个 MP3，不做按目标时长拆分/合并"""
        for f in files:
            if self.stop_flag:
                break

            # 普通模式的一章就是一个稳定音轨。序号必须继承 EPUB→TXT
            # 阶段写入文件名的原始章节编号，不能按本次勾选列表重新计数。
            original_chapter_number = get_original_chapter_number(f)

            existing_outputs = find_existing_outputs_for_txt(out_dir, f)
            if existing_outputs:
                if len(existing_outputs) == 1:
                    dur = self.get_mp3_duration_str(existing_outputs[0])
                    self.set_file_status(f, f"已存在(时长{dur})" if dur else "已存在", spinning=False)
                else:
                    self.set_file_status(f, f"已存在({len(existing_outputs)}段)", spinning=False)
                self.set_file_progress(f, 100.0)
                continue

            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue

            text = text.strip()
            if not text:
                self.set_file_status(f, "跳过：空文本", spinning=False)
                self.set_file_progress(f, 100.0)
                continue

            _process_audio_chunk(
                text=text,
                out_dir=out_dir,
                part_num=1,
                file_list=[f],
                edge_tts_wrapper=self.edge,
                voice_var=self.voice_var,
                speed_var=self.speed_var,
                pitch_var=self.pitch_var,
                volume_var=self.volume_var,
                set_file_status=self.set_file_status,
                set_file_progress=self.set_file_progress,
                set_error=self.set_error,
                get_mp3_duration_str=self.get_mp3_duration_str,
                seconds_to_str=self.seconds_to_str,
                stop_flag_check=lambda: self.stop_flag,
                tts_with_retry=self.tts_with_retry,
                cover_path=self.current_cover_path,
                book_metadata=self.current_book_metadata,
                split_total=1,
                track_number=original_chapter_number,
            )

    def generate_smart_by_target(self, files: List[str], txt_dir: str, out_dir: str):
        """智能模式：按目标时长进行拆分/合并"""
        if not self.merge_var.get():
            self.generate_plain_files(files, txt_dir, out_dir)
            return

        # 仍沿用你原来的逻辑：
        # - merge_var=True 时走“合并模式”
        # - 单个文件太长时仍会拆分
        self.generate_merged_files(files, txt_dir, out_dir)

    def generate_single_files(self, files: List[str], txt_dir: str, out_dir: str):
        """单文件转换模式 - 按目标时长分割"""
        target_minutes = int(self.target_duration_var.get())
        target_minutes = max(10, min(120, target_minutes))
        track_number = 1

        for f in files:
            if self.stop_flag:
                break

            existing_outputs = find_existing_outputs_for_txt(out_dir, f)
            if existing_outputs:
                if len(existing_outputs) == 1:
                    dur = self.get_mp3_duration_str(existing_outputs[0])
                    self.set_file_status(f, f"已存在(时长{dur})" if dur else "已存在", spinning=False)
                else:
                    self.set_file_status(f, f"已存在({len(existing_outputs)}段)", spinning=False)
                self.set_file_progress(f, 100.0)
                continue

            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue

            text = text.strip()
            file_duration = self.estimate_duration(text)

            if file_duration > target_minutes:
                sub_parts = self.split_long_text(text, target_minutes, f)
                split_total = len(sub_parts)
                created_split_outputs = []

                for idx, (sub_text, sub_files) in enumerate(sub_parts, 1):
                    if self.stop_flag:
                        break
                    split_output = build_output_path(
                        out_dir,
                        sub_files,
                        part_num=idx,
                        split_total=split_total,
                    )
                    existed_before = os.path.exists(split_output)
                    result = _process_audio_chunk(
                        text=sub_text,
                        out_dir=out_dir,
                        part_num=idx,
                        file_list=sub_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry,
                        cover_path=self.current_cover_path,
                        book_metadata=self.current_book_metadata,
                        split_total=split_total,
                        track_number=track_number,
                    )
                    track_number += 1
                    if result == TTSResult.SUCCESS and not existed_before and os.path.exists(split_output):
                        created_split_outputs.append(split_output)
                    if result != TTSResult.SUCCESS:
                        self._remove_incomplete_split_outputs(created_split_outputs)
                        self.set_file_status(
                            f,
                            "已中断" if result == TTSResult.STOPPED else "失败：章节未完整生成",
                            spinning=False,
                        )
                        break
            else:
                _process_audio_chunk(
                    text=text,
                    out_dir=out_dir,
                    part_num=1,
                    file_list=[f],
                    edge_tts_wrapper=self.edge,
                    voice_var=self.voice_var,
                    speed_var=self.speed_var,
                    pitch_var=self.pitch_var,
                    volume_var=self.volume_var,
                    set_file_status=self.set_file_status,
                    set_file_progress=self.set_file_progress,
                    set_error=self.set_error,
                    get_mp3_duration_str=self.get_mp3_duration_str,
                    seconds_to_str=self.seconds_to_str,
                    stop_flag_check=lambda: self.stop_flag,
                    tts_with_retry=self.tts_with_retry,
                    cover_path=self.current_cover_path,
                    book_metadata=self.current_book_metadata,
                    track_number=track_number,
                )
                track_number += 1

    def generate_merged_files(self, files: List[str], txt_dir: str, out_dir: str):
        """合并模式 - 支持长文本分割"""
        target_duration = self.target_duration_var.get()
        part_num = 1
        current_text = ""
        current_duration = 0.0
        current_files = []

        for f in files:
            if self.stop_flag:
                break

            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue

            text = text.strip()
            file_duration = self.estimate_duration(text)

            if file_duration >= target_duration:
                if current_text and not self.stop_flag:
                    _process_audio_chunk(
                        text=current_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=current_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry,
                        cover_path=self.current_cover_path,
                        book_metadata=self.current_book_metadata,
                        track_number=part_num,
                    )
                    part_num += 1
                    current_text = ""
                    current_duration = 0.0
                    current_files = []

                sub_parts = self.split_long_text(text, target_duration, f)
                split_total = len(sub_parts)
                created_split_outputs = []

                for sub_idx, (sub_text, sub_files) in enumerate(sub_parts, 1):
                    if self.stop_flag:
                        break
                    split_output = build_output_path(
                        out_dir,
                        sub_files,
                        part_num=sub_idx,
                        split_total=split_total,
                    )
                    existed_before = os.path.exists(split_output)
                    result = _process_audio_chunk(
                        text=sub_text,
                        out_dir=out_dir,
                        part_num=sub_idx,
                        file_list=sub_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry,
                        cover_path=self.current_cover_path,
                        book_metadata=self.current_book_metadata,
                        split_total=split_total,
                        track_number=part_num,
                    )
                    part_num += 1
                    if result == TTSResult.SUCCESS and not existed_before and os.path.exists(split_output):
                        created_split_outputs.append(split_output)
                    if result != TTSResult.SUCCESS:
                        self._remove_incomplete_split_outputs(created_split_outputs)
                        self.set_file_status(
                            f,
                            "已中断" if result == TTSResult.STOPPED else "失败：章节未完整生成",
                            spinning=False,
                        )
                        break
                continue

            self.set_file_status(f, "等待合并", spinning=False)

            if current_duration + file_duration <= target_duration:
                current_text += ("\n\n" + text) if current_text else text
                current_duration += file_duration
                current_files.append(f)
            else:
                if current_text and not self.stop_flag:
                    _process_audio_chunk(
                        text=current_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=current_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry,
                        cover_path=self.current_cover_path,
                        book_metadata=self.current_book_metadata,
                        track_number=part_num,
                    )
                    part_num += 1

                current_text = text
                current_duration = file_duration
                current_files = [f]

        if current_text and not self.stop_flag:
            _process_audio_chunk(
                text=current_text,
                out_dir=out_dir,
                part_num=part_num,
                file_list=current_files,
                edge_tts_wrapper=self.edge,
                voice_var=self.voice_var,
                speed_var=self.speed_var,
                pitch_var=self.pitch_var,
                volume_var=self.volume_var,
                set_file_status=self.set_file_status,
                set_file_progress=self.set_file_progress,
                set_error=self.set_error,
                get_mp3_duration_str=self.get_mp3_duration_str,
                seconds_to_str=self.seconds_to_str,
                stop_flag_check=lambda: self.stop_flag,
                tts_with_retry=self.tts_with_retry,
                cover_path=self.current_cover_path,
                book_metadata=self.current_book_metadata,
                track_number=part_num,
            )
