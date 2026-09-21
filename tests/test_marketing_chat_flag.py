"""The marketing-group interactive chat switch.

Exercises the real Feishu handler rather than only the flag, so "disabled" is
proven to mean no executor call and no attachment staging.
"""

import json
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

# Configuration comes from tests/conftest.py, which runs before collection so
# app.config is read once with deterministic values.
import app.main as bridge  # noqa: E402


def marketing_payload(message_type: str, content: dict) -> dict:
    # Event and message ids must be unique: the handler dedupes at-least-once
    # redeliveries, so a reused id would silently skip a later call in the same
    # test session and make the enabled-path assertion fail.
    return {
        "header": {"event_type": "im.message.receive_v1",
                   "event_id": f"evt-{uuid.uuid4().hex}"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_user"}},
            "message": {
                "chat_id": "oc_marketing",
                "message_id": f"om_{uuid.uuid4().hex}",
                "message_type": message_type,
                "content": json.dumps(content),
            },
        },
    }


class MarketingChatGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_text_does_not_reach_the_executor(self):
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", False), \
             patch.object(bridge, "process_marketing_message", new_callable=AsyncMock) as dispatched:
            await bridge._handle_receive(marketing_payload("text", {"text": "hi"}))
            await __import__("asyncio").sleep(0)
        dispatched.assert_not_called()

    async def test_disabled_image_does_not_stage_an_attachment(self):
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", False), \
             patch.object(bridge, "stage_marketing_attachment", new_callable=AsyncMock) as staged:
            await bridge._handle_receive(
                marketing_payload("image", {"image_key": "img_key"})
            )
            await __import__("asyncio").sleep(0)
        staged.assert_not_called()

    async def test_disabled_file_does_not_stage_an_attachment(self):
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", False), \
             patch.object(bridge, "stage_marketing_attachment", new_callable=AsyncMock) as staged:
            await bridge._handle_receive(
                marketing_payload("file", {"file_key": "file_key", "file_name": "a.pdf"})
            )
            await __import__("asyncio").sleep(0)
        staged.assert_not_called()

    async def test_disabled_does_not_reply_into_the_group(self):
        """No chatty notice either - the group must stay quiet."""
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", False), \
             patch.object(bridge.feishu, "reply_text") as reply:
            await bridge._handle_receive(marketing_payload("text", {"text": "hi"}))
            await __import__("asyncio").sleep(0)
        reply.assert_not_called()

    async def test_enabled_still_dispatches(self):
        """The flag is opt-out, so the default path must keep working."""
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", True), \
             patch.object(bridge, "process_marketing_message", new_callable=AsyncMock) as dispatched:
            await bridge._handle_receive(marketing_payload("text", {"text": "hi"}))
            await __import__("asyncio").sleep(0)
        dispatched.assert_called_once()

    async def test_messages_elsewhere_are_unaffected(self):
        """A message in a different chat must not be swallowed by the gate."""
        payload = marketing_payload("text", {"text": "hi"})
        payload["event"]["message"]["chat_id"] = "oc_somewhere_else"
        with patch.object(bridge, "MARKETING_CHAT_ENABLED", False), \
             patch.object(bridge.feishu, "reply_text") as reply:
            await bridge._handle_receive(payload)
            await __import__("asyncio").sleep(0)
        reply.assert_not_called()


class MarketingFlagParsingTests(unittest.TestCase):
    def test_flag_is_opt_out(self):
        import importlib

        import app.config

        for raw, expected in (("", True), ("true", True), ("0", False),
                              ("false", False), ("no", False), ("off", False)):
            os.environ["MARKETING_CHAT_ENABLED"] = raw
            self.assertEqual(
                importlib.reload(app.config).MARKETING_CHAT_ENABLED, expected,
                f"value {raw!r}",
            )
        os.environ.pop("MARKETING_CHAT_ENABLED", None)
        importlib.reload(app.config)


if __name__ == "__main__":
    unittest.main()
