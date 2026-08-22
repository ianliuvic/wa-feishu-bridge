import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("FEISHU_APP_ID", "test-app")
os.environ.setdefault("FEISHU_APP_SECRET", "test-secret")
os.environ.setdefault("FEISHU_CHAT_ID", "oc_test")

from app.cards import codex_card
from app.feishu import FeishuClient


class _Response:
    def json(self):
        return {"code": 0, "msg": "success", "data": {}}


class FeishuClientTests(unittest.TestCase):
    def test_codex_progress_is_an_editable_interactive_card(self):
        card = codex_card("正在分析", "working")
        self.assertEqual(card["header"]["template"], "blue")
        self.assertEqual(card["elements"][0]["tag"], "markdown")
        self.assertEqual(card["elements"][0]["content"], "正在分析")

    @patch("app.feishu.httpx.patch", return_value=_Response())
    def test_update_card_edits_existing_bot_message(self, request):
        client = FeishuClient("app", "secret")
        client._token = "token"
        client._token_expires_at = 99999999999

        card = {"elements": [{"tag": "markdown", "content": "Codex 正在执行"}]}
        client.update_card("om_progress", card)

        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertTrue(args[0].endswith("/open-apis/im/v1/messages/om_progress"))
        self.assertEqual(json.loads(kwargs["json"]["content"]), card)


if __name__ == "__main__":
    unittest.main()
