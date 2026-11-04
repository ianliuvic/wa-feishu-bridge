import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.evolution import EvolutionMessage
from app.inquiry import InquiryNotifier


def _message(text="", jid="15550001@s.whatsapp.net", push_name="Buyer", media=None, alt="", mid=""):
    message = {"conversation": text} if media is None else {media: {}}
    return EvolutionMessage({
        "instance": "WA: test",
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": jid, "remoteJidAlt": alt, "fromMe": False, "id": mid or "MSG1"},
            "pushName": push_name,
            "messageType": "conversation" if media is None else "media",
            "message": message,
        },
    })


class FakeLLM:
    def __init__(self, results):
        self.results = list(results)

    def chat(self, user_text, system=None, temperature=0.7, max_tokens=1024, json_mode=False):
        assert json_mode is True
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return json.dumps(result)


def _true(summary="问 500 件比基尼报价", translation="你好，500 件比基尼多少钱？"):
    return {
        "is_inquiry": True, "confidence": 0.9, "reason": "询价",
        "summary": summary, "translation": translation,
    }


def _false(reason="只是问候"):
    return {
        "is_inquiry": False, "confidence": 0.9, "reason": reason,
        "summary": "", "translation": "",
    }


class FakeEvolution:
    def __init__(self, chats, history=False):
        self.chats = chats
        self.history = history

    def find_chats(self, instance):
        return self.chats

    def has_history(self, instance, jids, exclude_message_id=""):
        return self.history


class InquiryNotifierTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="wa-inquiry-", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.notifier = InquiryNotifier("https://hook.invalid", self.db_path)
        self.llm = FakeLLM([_true()])
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

    def test_existing_history_is_marked_known(self):
        self.evolution = FakeEvolution([], history=True)
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("We need 500 pcs bikini"), self.llm, self.evolution
            )
            self.assertFalse(sent)
            again = self.notifier.screen(
                _message("more details"), self.llm, self.evolution
            )
            self.assertFalse(again)
        post.assert_not_called()
        state = self.notifier._row("15550001@s.whatsapp.net")
        self.assertEqual(state["status"], "known")

    def test_alt_jid_matches_seeded_chat(self):
        self.evolution = FakeEvolution([{"remoteJid": "149529353576473@lid"}])
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("500 pcs bikini", jid="61439950014@s.whatsapp.net",
                         alt="149529353576473@lid"),
                self.llm, self.evolution,
            )
        self.assertFalse(sent)
        post.assert_not_called()

    def test_low_confidence_is_not_forwarded(self):
        self.llm = FakeLLM([{
            "is_inquiry": True, "confidence": 0.2, "reason": "vague",
            "summary": "", "translation": "",
        }])
        with patch.object(self.notifier, "post") as post:
            sent = self.notifier.screen(
                _message("we want cooperation"), self.llm, self.evolution
            )
        self.assertFalse(sent)
        post.assert_not_called()

    def test_greeting_then_inquiry_is_forwarded(self):
        """A greeting must not lock the sender: the next message is judged."""
        self.llm = FakeLLM([_false("只是问候"), _true()])
        with patch.object(self.notifier, "post", return_value=True) as post:
            first = self.notifier.screen(_message("Hi"), self.llm, self.evolution)
            self.assertFalse(first)
            post.assert_not_called()
            second = self.notifier.screen(
                _message("We need 500 pcs bikini, please quote"), self.llm, self.evolution
            )
        self.assertTrue(second)
        post.assert_called_once()
        state = self.notifier._row("15550001@s.whatsapp.net")
        self.assertEqual(state["status"], "sent")
        self.assertEqual(state["attempts"], 1)

    def test_five_non_inquiries_stop_screening(self):
        self.llm = FakeLLM([_false(), _false(), _false(), _false(), _false()])
        with patch.object(self.notifier, "post") as post:
            for text in ("Hi", "hello?", "how are you", "you there", "ok"):
                self.assertFalse(self.notifier.screen(_message(text), self.llm, self.evolution))
            # A later inquiry is ignored: the window is closed.
            self.llm = FakeLLM([_true()])
            self.assertFalse(
                self.notifier.screen(_message("500 pcs bikini?"), self.llm, self.evolution)
            )
        post.assert_not_called()
        state = self.notifier._row("15550001@s.whatsapp.net")
        self.assertEqual(state["status"], "rejected")
        self.assertEqual(state["attempts"], 5)

    def test_media_does_not_consume_an_attempt(self):
        self.llm = FakeLLM([_true()])
        with patch.object(self.notifier, "post", return_value=True):
            self.notifier.screen(_message("caption", media="imageMessage"), self.llm, self.evolution)
            state = self.notifier._row("15550001@s.whatsapp.net")
            self.assertIsNone(state)
            self.assertTrue(
                self.notifier.screen(_message("Need 300 pcs swimwear"), self.llm, self.evolution)
            )

    def test_old_schema_is_migrated(self):
        import sqlite3
        db = sqlite3.connect(self.db_path)
        db.execute(
            "CREATE TABLE seen (jid TEXT PRIMARY KEY, first_seen TEXT NOT NULL, push_name TEXT, "
            "seeded INTEGER NOT NULL DEFAULT 0, verdict INTEGER, reason TEXT)"
        )
        db.execute("INSERT INTO seen VALUES ('seed@s.whatsapp.net','2026-01-01 00:00:00','Old',1,NULL,'')")
        db.execute("INSERT INTO seen VALUES ('sent@s.whatsapp.net','2026-01-01 00:00:00','Sent',0,1,'')")
        db.execute("INSERT INTO seen VALUES ('rej@s.whatsapp.net','2026-01-01 00:00:00','Rej',0,0,'')")
        db.commit()
        db.close()
        notifier = InquiryNotifier("https://hook.invalid", self.db_path)
        self.assertEqual(notifier._row("seed@s.whatsapp.net")["status"], "known")
        self.assertEqual(notifier._row("sent@s.whatsapp.net")["status"], "sent")
        self.assertEqual(notifier._row("rej@s.whatsapp.net")["status"], "rejected")
        notifier.close()

    def test_card_is_read_only(self):
        card = self.notifier.build_card(
            _message("hi"), "摘要", "original", "translation"
        )
        serialized = json.dumps(card)
        self.assertNotIn('"action"', serialized)
        self.assertNotIn('"button"', serialized)
        self.assertEqual(card["header"]["template"], "green")
        panels = [e for e in card["body"]["elements"] if e["tag"] == "collapsible_panel"]
        self.assertTrue(panels)
        self.assertTrue(all(p["elements"][0]["tag"] == "div" for p in panels))
        self.assertFalse(card["config"]["wide_screen_mode"])


if __name__ == "__main__":
    unittest.main()
