"""Minimal Feishu (Lark) Open Platform client: tenant token + send text to a chat."""

import json
import logging
import re
import time
from urllib.parse import unquote

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
        return self._send(url, headers, body)

    def reply_text(self, message_id: str, text: str) -> dict:
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages/{message_id}/reply"
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        return self._send(url, headers, body)

    def update_text(self, message_id: str, text: str) -> dict:
        """Replace the content of a text message previously sent by the bot."""
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages/{message_id}"
        headers = {"Authorization": f"Bearer {token}"}
        body = {"content": json.dumps({"text": text}, ensure_ascii=False)}
        resp = httpx.patch(url, json=body, headers=headers, timeout=15)
        data = self._check(resp, "update message")
        logger.info("updated message %s", message_id[:24])
        return data

    def send_card(self, chat_id: str, card: dict) -> dict:
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        return self._send(url, headers, body)

    def send_file_message(self, chat_id: str, file_key: str, file_name: str) -> dict:
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key, "file_name": file_name}, ensure_ascii=False),
        }
        return self._send(url, headers, body)

    def _send(self, url: str, headers: dict, body: dict) -> dict:
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
        logger.info("sent message to chat %s (msg_id=%s)", body.get("receive_id"), (data.get("data") or {}).get("message_id"))
        return data

    def upload_image(self, data: bytes, mimetype: str) -> str:
        """Upload an image and return its image_key."""
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/images"
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.post(
            url,
            headers=headers,
            data={"image_type": "message"},
            files={"image": ("image", data, mimetype)},
            timeout=60,
        )
        payload = self._check(resp, "upload image")
        image_key = (payload.get("data") or {}).get("image_key")
        if not image_key:
            raise RuntimeError(f"Feishu image upload: no image_key in {payload}")
        logger.info("uploaded image, key=%s", image_key[:24])
        return image_key

    def upload_file(self, data: bytes, file_name: str, mimetype: str) -> str:
        """Upload a file and return its file_key."""
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/files"
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.post(
            url,
            headers=headers,
            data={"file_type": "stream", "file_name": file_name},
            files={"file": (file_name, data, mimetype)},
            timeout=60,
        )
        payload = self._check(resp, "upload file")
        file_key = (payload.get("data") or {}).get("file_key")
        if not file_key:
            raise RuntimeError(f"Feishu file upload: no file_key in {payload}")
        logger.info("uploaded file %s, key=%s", file_name, file_key[:24])
        return file_key

    @staticmethod
    def _check(resp, what: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu {what} error: code={data.get('code')} msg={data.get('msg')}")
        return data

    def download_resource(self, message_id: str, file_key: str, res_type: str = "image") -> tuple[bytes, str, str]:
        """Download an image/file resource attached to a message.

        Returns (bytes, mimetype, filename). Filename is parsed from the
        Content-Disposition header when present, else derived from the mimetype.
        """
        token = self._get_token()
        url = f"{self.api_base}/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type={res_type}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get(url, headers=headers, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Feishu resource download failed: status={resp.status_code} body={resp.text[:300]}"
            )
        mimetype = resp.headers.get("content-type", "application/octet-stream")
        file_name = self._filename_from_disposition(
            resp.headers.get("content-disposition", ""), mimetype
        )
        logger.info(
            "downloaded feishu resource msg=%s key=%s (%d bytes, %s)",
            message_id[:24], file_key[:20], len(resp.content), file_name,
        )
        return resp.content, mimetype, file_name

    @staticmethod
    def _filename_from_disposition(disposition: str, mimetype: str) -> str:
        if not disposition:
            return FeishuClient._default_filename(mimetype)
        m = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE) or re.search(
            r'filename="?([^";]+)"?', disposition, re.IGNORECASE
        )
        if m:
            name = unquote(m.group(1)).strip()
            if name:
                return name
        return FeishuClient._default_filename(mimetype)

    @staticmethod
    def _default_filename(mimetype: str) -> str:
        ext_map = {
            "image/jpeg": "image.jpg",
            "image/png": "image.png",
            "image/gif": "image.gif",
            "image/webp": "image.webp",
            "application/pdf": "document.pdf",
            "text/plain": "file.txt",
            "application/msword": "document.doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document.docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet.xlsx",
        }
        return ext_map.get(mimetype, "file.bin")
