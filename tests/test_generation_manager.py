import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from generation_manager import (
    GenerationMixin,
    classify_task_status,
    summarize_task,
)
from audio_processor import build_output_path
from models import TTSRequestStopped, TTSRequestTimeout, TTSResult


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class TestGeneration(GenerationMixin):
    def __init__(self):
        self.stop_flag = False
        self.tts_cancel_event = threading.Event()
        self.edge = Mock()
        self.voice_var = Value("voice")
        self.speed_var = Value(0.8)
        self.pitch_var = Value(0)
        self.volume_var = Value(100)
        self.wpm_var = Value(240)
        self.statuses = []
        self.errors = []

    def set_file_status(self, iid, status, **_kwargs):
        self.statuses.append((iid, status))

    def set_error(self, iid, error):
        self.errors.append((iid, error))

    def log_tts_attempt(self, **_kwargs):
        pass


class StoppedGeneration(GenerationMixin):
    def __init__(self):
        self.stop_flag = True
        self.tts_cancel_event = threading.Event()
        self.edge = Mock()
        self.wpm_var = Value(240)

    def log_tts_attempt(self, **_kwargs):
        pass


class GenerationManagerTests(unittest.TestCase):
    def test_elapsed_time_uses_fixed_hour_minute_second_format(self):
        self.assertEqual(GenerationMixin.format_elapsed_time(0), "00:00:00")
        self.assertEqual(GenerationMixin.format_elapsed_time(3661.9), "01:01:01")
        self.assertEqual(GenerationMixin.format_elapsed_time(90061), "25:01:01")

    def test_stopped_task_does_not_start_tts_retry(self):
        generator = StoppedGeneration()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "should_not_exist.mp3"
            result = generator.tts_with_retry(
                text="不应发送的测试文本",
                output_file=str(output_file),
                max_retries=3,
            )

        self.assertEqual(result, TTSResult.STOPPED)
        generator.edge.text_to_speech.assert_not_called()

    def test_normal_segment_success(self):
        generator = TestGeneration()

        def write_audio(**kwargs):
            Path(kwargs["output_file"]).write_bytes(b"audio")

        generator.edge.text_to_speech.side_effect = write_audio
        with tempfile.TemporaryDirectory() as temp_dir:
            result = generator.tts_with_retry(
                "正常正文",
                str(Path(temp_dir) / "segment.mp3"),
                iid_for_error="001.txt",
            )

        self.assertEqual(result, TTSResult.SUCCESS)
        generator.edge.text_to_speech.assert_called_once()

    def test_timeout_retries_then_succeeds(self):
        generator = TestGeneration()
        calls = 0

        def timeout_then_write(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TTSRequestTimeout("timeout")
            Path(kwargs["output_file"]).write_bytes(b"audio")

        generator.edge.text_to_speech.side_effect = timeout_then_write
        with tempfile.TemporaryDirectory() as temp_dir:
            result = generator.tts_with_retry(
                "重试正文",
                str(Path(temp_dir) / "segment.mp3"),
                iid_for_error="001.txt",
            )

        self.assertEqual(result, TTSResult.SUCCESS)
        self.assertEqual(calls, 2)
        self.assertIn(("001.txt", "正在重试 · 1/2"), generator.statuses)

    def test_three_timeouts_fail_without_output(self):
        generator = TestGeneration()
        generator.edge.text_to_speech.side_effect = TTSRequestTimeout("timeout")

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "segment.mp3"
            result = generator.tts_with_retry(
                "连续超时正文",
                str(output),
                iid_for_error="001.txt",
            )

            self.assertFalse(output.exists())

        self.assertEqual(result, TTSResult.FAILED)
        self.assertEqual(generator.edge.text_to_speech.call_count, 3)
        self.assertIn(("001.txt", "正在重试 · 2/2"), generator.statuses)

    def test_user_stop_cleans_partial_segment(self):
        generator = TestGeneration()

        def stopped_request(**kwargs):
            Path(kwargs["output_file"]).write_bytes(b"partial")
            raise TTSRequestStopped("stopped")

        generator.edge.text_to_speech.side_effect = stopped_request
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "segment.mp3"
            result = generator.tts_with_retry(
                "停止正文",
                str(output),
                iid_for_error="001.txt",
            )
            self.assertFalse(output.exists())

        self.assertEqual(result, TTSResult.STOPPED)

    @patch("generation_manager._process_audio_chunk")
    def test_failed_chapter_does_not_block_next_chapter(self, process_mock):
        generator = TestGeneration()
        generator.txt_dir = Value("/tmp")
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="正文")
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()
        process_mock.side_effect = [TTSResult.FAILED, TTSResult.SUCCESS]

        generator.generate_plain_files(["001.txt", "002.txt"], "/tmp", "/tmp/out")

        self.assertEqual(process_mock.call_count, 2)

    @patch("generation_manager._process_audio_chunk", return_value=TTSResult.SUCCESS)
    def test_plain_mode_preserves_original_numbers_when_selection_has_gaps(self, process_mock):
        generator = TestGeneration()
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="正文")
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()

        selected_files = ["001 前言.txt", "003 第一章.txt", "005 第三章.txt"]
        generator.generate_plain_files(selected_files, "/tmp", "/tmp/out")

        self.assertEqual(
            [call.kwargs["track_number"] for call in process_mock.call_args_list],
            [1, 3, 5],
        )

    @patch("generation_manager._process_audio_chunk", return_value=TTSResult.SUCCESS)
    def test_plain_mode_keeps_original_number_across_later_batches(self, process_mock):
        generator = TestGeneration()
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="正文")
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()

        generator.generate_plain_files(
            ["021 第二十一章.txt", "022 第二十二章.txt", "040 第四十章.txt"],
            "/tmp",
            "/tmp/out",
        )

        self.assertEqual(
            [call.kwargs["track_number"] for call in process_mock.call_args_list],
            [21, 22, 40],
        )

    @patch("generation_manager._process_audio_chunk", return_value=TTSResult.SUCCESS)
    def test_plain_mode_single_chapter_and_retry_keep_original_number(self, process_mock):
        generator = TestGeneration()
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="正文")
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()

        generator.generate_plain_files(["037 某章节.txt"], "/tmp", "/tmp/out")
        generator.generate_plain_files(["054 失败后重试.txt"], "/tmp", "/tmp/out")

        self.assertEqual(
            [call.kwargs["track_number"] for call in process_mock.call_args_list],
            [37, 54],
        )

    @patch("generation_manager._process_audio_chunk", return_value=TTSResult.SUCCESS)
    def test_merged_mode_keeps_final_audio_block_numbering(self, process_mock):
        generator = TestGeneration()
        generator.target_duration_var = Value(40)
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="正文")
        generator.estimate_duration = Mock(return_value=10)
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()

        generator.generate_merged_files(
            ["021 第二十一章.txt", "022 第二十二章.txt"],
            "/tmp",
            "/tmp/out",
        )

        process_mock.assert_called_once()
        self.assertEqual(process_mock.call_args.kwargs["track_number"], 1)
        self.assertEqual(
            process_mock.call_args.kwargs["file_list"],
            ["021 第二十一章.txt", "022 第二十二章.txt"],
        )

    @patch("generation_manager._process_audio_chunk")
    def test_merged_long_chapter_stops_after_middle_part_failure(self, process_mock):
        generator = TestGeneration()
        generator.target_duration_var = Value(40)
        generator.current_cover_path = None
        generator.current_book_metadata = None
        generator.read_text_file = Mock(return_value="超长正文")
        generator.estimate_duration = Mock(return_value=80)
        generator.split_long_text = Mock(return_value=[
            ("第一部分", ["001.txt"]),
            ("第二部分", ["001.txt"]),
            ("第三部分", ["001.txt"]),
        ])
        generator.get_mp3_duration_str = Mock(return_value="")
        generator.seconds_to_str = Mock(return_value="")
        generator.set_file_progress = Mock()

        with tempfile.TemporaryDirectory() as out_dir:
            calls = 0

            def generate_then_fail(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    output = Path(
                        build_output_path(
                            out_dir,
                            kwargs["file_list"],
                            kwargs["part_num"],
                            kwargs["split_total"],
                        )
                    )
                    output.write_bytes(b"partial-chapter")
                    return TTSResult.SUCCESS
                return TTSResult.FAILED

            process_mock.side_effect = generate_then_fail
            generator.generate_merged_files(["001.txt"], "/tmp", out_dir)

            self.assertEqual(process_mock.call_count, 2)
            self.assertEqual(list(Path(out_dir).glob("*.mp3")), [])
            self.assertIn(("001.txt", "失败：章节未完整生成"), generator.statuses)

    def test_task_summary_counts_all_terminal_states(self):
        files = ["完成.txt", "跳过.txt", "失败.txt", "停止.txt", "处理中.txt"]
        statuses = {
            "完成.txt": "已完成（时长10:00）",
            "跳过.txt": "已存在(跳过)",
            "失败.txt": "合成失败 __tmp.mp3",
            "停止.txt": "已中断",
            "处理中.txt": "合成中（1/2）",
        }
        progress = {"处理中.txt": 50}

        summary = summarize_task(files, statuses, progress)

        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["stopped"], 1)
        self.assertEqual(summary["handled"], 4)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["percent"], 90)

    def test_status_classifier_handles_existing_output(self):
        self.assertEqual(
            classify_task_status("已存在(时长12:30)"),
            "skipped",
        )


if __name__ == "__main__":
    unittest.main()
