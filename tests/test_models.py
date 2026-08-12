import unittest
from unittest.mock import patch

from models import EdgeTTSWrapper, VOICE_MAPPING, clean_text_from_html_bytes


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, rate, pitch, volume):
        self.__class__.calls.append(
            {
                "text": text,
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
                "volume": volume,
            }
        )

    async def save(self, output_file):
        self.output_file = output_file


class EdgeTTSWrapperTests(unittest.TestCase):
    def setUp(self):
        FakeCommunicate.calls.clear()

    @patch("models.edge_tts.list_voices")
    def test_initialization_does_not_request_network(self, list_voices):
        wrapper = EdgeTTSWrapper()

        list_voices.assert_not_called()
        self.assertEqual(wrapper.voices, list(VOICE_MAPPING.keys()))

    def test_resolve_voice_accepts_label_and_voice_code(self):
        wrapper = EdgeTTSWrapper()
        voice_code = VOICE_MAPPING["晓晓(女)"]

        self.assertEqual(wrapper._resolve_voice_code("晓晓(女)"), voice_code)
        self.assertEqual(wrapper._resolve_voice_code(voice_code), voice_code)

    @patch("models.edge_tts.Communicate", FakeCommunicate)
    def test_text_to_speech_requests_synthesis_once(self):
        wrapper = EdgeTTSWrapper()

        wrapper.text_to_speech(
            text="测试文本",
            voice="晓晓(女)",
            speed=1.0,
            pitch=0,
            volume=0,
            output_file="unused.mp3",
        )

        self.assertEqual(len(FakeCommunicate.calls), 1)
        self.assertEqual(
            FakeCommunicate.calls[0]["voice"],
            VOICE_MAPPING["晓晓(女)"],
        )


class HtmlTextCleaningTests(unittest.TestCase):
    def test_body_text_containing_note_word_is_not_dropped(self):
        html = """
        <html><head><title>第一章 中国为什么叫中国</title></head><body>
          <div>
            <h1>第一章</h1>
            <h1>中国为什么叫中国</h1>
            <p>中国文明的起点</p>
            <p>这是正文，作者在这里讨论一个注释中的观点。</p>
            <aside epub:type="footnote"><p>这是应该删除的脚注。</p></aside>
            <p>这是脚注之后仍应保留的正文。</p>
          </div>
        </body></html>
        """

        _title, text = clean_text_from_html_bytes(html.encode("utf-8"))

        self.assertIn("中国文明的起点", text)
        self.assertIn("注释中的观点", text)
        self.assertIn("脚注之后仍应保留", text)
        self.assertNotIn("应该删除的脚注", text)


if __name__ == "__main__":
    unittest.main()
