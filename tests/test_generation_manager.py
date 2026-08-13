import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from generation_manager import (
    GenerationMixin,
    classify_task_status,
    summarize_task,
)


class StoppedGeneration(GenerationMixin):
    def __init__(self):
        self.stop_flag = True
        self.edge = Mock()


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

        self.assertFalse(result)
        generator.edge.text_to_speech.assert_not_called()

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
