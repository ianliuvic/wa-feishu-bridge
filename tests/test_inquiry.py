import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.evolution import EvolutionMessage
from app.inquiry import InquiryNotifier


def _message(text="", jid="15550001@s.whatsapp.net", push_name="Buyer", media=None):
    message = {"conversation": text} if media is None else {media: {}}
    return EvolutionMessage({
        "instance": "WA: test",
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": jid, "fromMe": False},
            "pushName": push_name,
            "messageType": "conversation" if media is None else "media",
            "message": message,
        },
    })


class FakeLLM:
    def __init__(self, result):
        self.result = result

    def chat(self, user_text, system=None, temperature=0.7, max_tokens=1024, json_mode=False):
        assert json_mode is True
        return json.dumps(self.result)


class FakeEvolution:
    def __init__(self, chats):
        self.chats = chats

    def find_chats(self, instance):
        return self.chats


class InquiryNotifierTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="wa-inquiry-", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.notifier = InquiryNotifier("https://hook.invalid", self.db_path)
        self.llm = FakeLLM({
            "is_inquiry": True, "confidence": 0.9, "reason": "询价",
            "summary": "问 500 件比基尼报价", "translation": "你好，500 件比基尼多少钱？",
        })
        self.evolution = FakeEvolution([{"remoteJid": "15550000@s.whatsapp.net"}])

    def tearDown(self):
        self.notifier.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_new_sender_inquiry_is_forwarded_once(self):
        with patch.object(self.notifier, "post", return_value=True) as post:
            sent = self.notifier.screen(
                _message("Hi, how much for 500 pcs bikini?"), self.llm, self.evolution
            )
            self.assertTrue(sent)
            post.assert_called_once()
            again = self.notifier.screen(
                _message("Hello again"), self.llm, self.evolution
            )
            self.assertFalse(again)
            post.assert_called_once()

    def test_known_chat_is_not_a_new_lead(self):
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("price?", jid="15550000@s.whatsapp.net"), self.llm, self.evolution
            )
        self.assertFalse(sent)
        post.assert_not_called()

    def test_media_first_message_is_skipped_but_not_consumed(self):
        with patch.object(self.notifier, "post", return_value=True) as post:
            sent = self.notifier.screen(
                _message("caption", media="imageMessage"), self.llm, self.evolution
            )
            self.assertFalse(sent)
            post.assert_not_called()
            # The next text message still counts as the first text message.
            sent = self.notifier.screen(
                _message("Need 300 pcs swimwear, quote please"), self.llm, self.evolution
            )
        self.assertTrue(sent)

    def test_group_message_is_ignored(self):
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("group hello", jid="120363000000000000@g.us"), self.llm, self.evolution
            )
        self.assertFalse(sent)
        post.assert_not_called()

    def test_low_confidence_is_not_forwarded(self):
        self.llm.result = {
            "is_inquiry": True, "confidence": 0.2, "reason": "vague",
            "summary": "", "translation": "",
        }
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("we want cooperation"), self.llm, self.evolution
            )
        self.assertFalse(sent)
        post.assert_not_called()

    def test_card_is_read_only(self):
        card = self.notifier.build_card(
            _message("hi"), "摘要", "original", "translation"
        )
        serialized = json.dumps(card)
        self.assertNotIn('"action"', serialized)
        self.assertNotIn('"button"', serialized)
        self.assertEqual(card["header"]["template"], "green")


if __name__ == "__main__":
    unittest.main()
