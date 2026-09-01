import unittest

from app.autoreply import ENGLISH_FALLBACK, SYSTEM_PROMPT, _ensure_english


class AutoReplyLanguageTests(unittest.TestCase):
    def test_prompt_requires_english_only(self):
        self.assertIn("Reply ONLY in English", SYSTEM_PROMPT)

    def test_chinese_model_output_is_replaced_with_english(self):
        reply = _ensure_english("您好，我们早上八点后回复。")
        self.assertEqual(reply, ENGLISH_FALLBACK)
        self.assertTrue(reply.isascii())

    def test_english_model_output_is_preserved(self):
        reply = "Thanks for your message. We will reply after 8:00 AM China time."
        self.assertEqual(_ensure_english(reply), reply)


if __name__ == "__main__":
    unittest.main()
