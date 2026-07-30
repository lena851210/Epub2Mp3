import unittest
from unittest.mock import patch

from models import EdgeTTSWrapper, VOICE_MAPPING


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


if __name__ == "__main__":
    unittest.main()
