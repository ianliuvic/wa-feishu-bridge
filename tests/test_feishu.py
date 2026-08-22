import json
import unittest
from unittest.mock import patch

from app.feishu import FeishuClient


class _Response:
    def json(self):
        return {"code": 0, "msg": "success", "data": {}}


class FeishuClientTests(unittest.TestCase):
    @patch("app.feishu.httpx.patch", return_value=_Response())
    def test_update_text_edits_existing_bot_message(self, request):
        client = FeishuClient("app", "secret")
        client._token = "token"
        client._token_expires_at = 99999999999

        client.update_text("om_progress", "Codex 正在执行")

        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertTrue(args[0].endswith("/open-apis/im/v1/messages/om_progress"))
        self.assertEqual(
            json.loads(kwargs["json"]["content"]), {"text": "Codex 正在执行"}
        )


if __name__ == "__main__":
    unittest.main()
