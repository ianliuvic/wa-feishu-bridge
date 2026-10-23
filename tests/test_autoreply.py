import unittest

from app.autoreply import (
    FALLBACK_REPLY,
    MEDIA_INPUT_NOTE,
    SYSTEM_PROMPT,
    _customer_text,
    _ensure_reply,
)


class _Evt:
    def __init__(self, text, media_type="none"):
        self.text = text
        self.media_type = media_type


class AutoReplyLanguageTests(unittest.TestCase):
    def test_prompt_follows_customer_language(self):
        self.assertIn("reply in that SAME language", SYSTEM_PROMPT)
        self.assertIn("Spanish -> Spanish", SYSTEM_PROMPT)
        self.assertNotIn("Reply ONLY in English", SYSTEM_PROMPT)

    def test_spanish_model_output_is_preserved(self):
        reply = (
            "¡Gracias por su mensaje! Nuestro equipo está descansando y le "
            "responderá después de las 8:00 (hora de China)."
        )
        self.assertEqual(_ensure_reply(reply), reply)

    def test_chinese_model_output_is_preserved_for_chinese_customer(self):
        reply = "您好，我们已收到您的消息，团队正在休息，将于中国时间早上8点后回复。"
        self.assertEqual(_ensure_reply(reply), reply)

    def test_empty_model_output_falls_back_to_english(self):
        self.assertEqual(_ensure_reply("   "), FALLBACK_REPLY)

    def test_customer_text_is_passed_through_for_detection(self):
        text = "Hola, busco trajes de baño por mayor"
        self.assertEqual(_customer_text(_Evt(text)), text)

    def test_media_message_uses_neutral_english_note(self):
        self.assertEqual(_customer_text(_Evt("[非文字消息]", "image")), MEDIA_INPUT_NOTE)
        self.assertEqual(_customer_text(_Evt("[非文字消息]", "audio")), MEDIA_INPUT_NOTE)


if __name__ == "__main__":
    unittest.main()
