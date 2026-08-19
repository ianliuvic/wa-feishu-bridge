"""Minimal Feishu (Lark) Open Platform client: tenant token + send text to a chat."""

import json
import logging
import time

import httpx

logger = logging.getLogger("bridge.feishu")


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, api_base: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_base = api_base
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        url = f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal"
        resp = httpx.post(url, json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu token error: code={data.get('code')} msg={data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + int(data.get("expire", 7200))
        logger.info("refreshed tenant_access_token, expires in %ss", data.get("expire"))
        return self._token

    def send_text(self, chat_id: str, text: str) -> dict:
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=15)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if data.get("code") != 0:
            raise RuntimeError(
                f"Feishu send error: code={data.get('code')} msg={data.get('msg')}"
            )
        logger.info("sent text message to chat %s (msg_id=%s)", chat_id, (data.get("data") or {}).get("message_id"))
        return data
