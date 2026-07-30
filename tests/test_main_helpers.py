import unittest

from main import AudiobookGenerator


class MainHelperTests(unittest.TestCase):
    def test_no_audio_error_is_translated_to_actionable_chinese(self):
        message = AudiobookGenerator.format_tts_error(
            RuntimeError(
                "No audio was received. Please verify that your parameters are correct."
            )
        )

        self.assertIn("没有返回音频", message)
        self.assertIn("自动重试 3 次", message)
        self.assertIn("刷新列表", message)


if __name__ == "__main__":
    unittest.main()
