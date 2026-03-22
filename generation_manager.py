# -*- coding: utf-8 -*-
"""
音频生成与转换流程模块
"""

import os
import time
import threading
from typing import List, Tuple, Optional
from tkinter import messagebox

from audio_processor import _process_audio_chunk


class GenerationMixin:
    """音频生成相关方法"""

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
        var = self.progress_vars.get(iid)
        if var is None:
            import tkinter as tk
            var = tk.DoubleVar(value=0.0)
            self.progress_vars[iid] = var
        self.root.after(0, lambda v=var, p=percent: v.set(max(0.0, min(100.0, float(p)))))

    def set_error(self, iid: str, exc: Exception):
        """设置错误信息"""
        self.error_detail[iid] = str(exc)

    def tts_with_retry(self, text: str, output_file: str, iid_for_error: Optional[str] = None, max_retries: int = 3) -> bool:
        """TTS 转换 - 支持重试"""
        last_exc = None
        parent = os.path.dirname(output_file) or "."
        os.makedirs(parent, exist_ok=True)

        for n in range(1, max_retries + 1):
            try:
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except Exception:
                        pass

                self.edge.text_to_speech(
                    text=text,
                    voice=self.voice_var.get(),
                    speed=self.speed_var.get(),
                    pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(),
                    output_file=output_file
                )

                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    return True
                raise RuntimeError(f"TTS输出为空或未生成: {os.path.basename(output_file)}")

            except Exception as e:
                last_exc = e
                if n < max_retries:
                    time.sleep(min(1.5, 0.5 * n))

        if iid_for_error:
            self.set_error(iid_for_error, last_exc)
        return False

    def start_generation(self):
        """开始转换任务"""
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

        self.stop_flag = False

        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，若分段>1将无法合并；请先安装 ffmpeg。")

        self.update_action_buttons_state()
        self.set_status("开始转换任务...")
        threading.Thread(target=self.generate, daemon=True).start()

    def stop_generation(self):
        """停止转换任务"""
        self.stop_flag = True
        self.set_status("用户请求停止，正在中止任务...")

    def generate(self):
        """生成有声书"""
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            self.root.after(0, lambda: messagebox.showerror("错误", "请选择有效的TXT目录"))
            return

        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True)

        files = self.get_selected_files()
        if not files:
            self.set_status("请先在文件列表中勾选至少一个TXT文件。")
            self.root.after(0, lambda: messagebox.showwarning("提示", "请在文件列表中勾选至少一个TXT文件。"))
            return

        if not self.merge_var.get():
            self.generate_single_files(files, txt_dir, out_dir)
        else:
            self.generate_merged_files(files, txt_dir, out_dir)

        if self.stop_flag:
            for f in files:
                cur = self.files_tree.set(f, "status")
                if cur not in ("✅ 已完成", "✅ 已完成，", "失败", "已存在(跳过)"):
                    self.set_file_status(f, "已中断", spinning=False)
            self.set_status("任务已中断。")
        else:
            self.set_status("所有任务处理完成。")

    def generate_single_files(self, files: List[str], txt_dir: str, out_dir: str):
        """单文件转换模式 - 按目标时长分割"""
        target_minutes = int(self.target_duration_var.get())
        target_minutes = max(10, min(120, target_minutes))

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

            if file_duration > target_minutes:
                sub_parts = self.split_long_text(text, target_minutes, f)
                split_total = len(sub_parts)

                for idx, (sub_text, sub_files) in enumerate(sub_parts, 1):
                    if self.stop_flag:
                        break
                    _process_audio_chunk(
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
                        split_total=split_total
                    )
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
                    tts_with_retry=self.tts_with_retry
                )

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
                        tts_with_retry=self.tts_with_retry
                    )
                    part_num += 1
                    current_text = ""
                    current_duration = 0.0
                    current_files = []

                sub_parts = self.split_long_text(text, target_duration, f)
                split_total = len(sub_parts)

                for sub_idx, (sub_text, sub_files) in enumerate(sub_parts, 1):
                    if self.stop_flag:
                        break
                    _process_audio_chunk(
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
                        split_total=split_total
                    )
                    part_num += 1
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
                        tts_with_retry=self.tts_with_retry
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
                tts_with_retry=self.tts_with_retry
            )