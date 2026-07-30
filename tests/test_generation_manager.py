import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from generation_manager import GenerationMixin


class StoppedGeneration(GenerationMixin):
    def __init__(self):
        self.stop_flag = True
        self.edge = Mock()


class GenerationManagerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
