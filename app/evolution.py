"""Parsing of Evolution API webhook payloads + a small Evolution REST client."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger("bridge.evolution")

BEIJING_TZ = timezone(timedelta(hours=8))


class EvolutionMessage:
    """Wraps an Evolution API webhook payload (event: messages.upsert)."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.instance = str(payload.get("instance") or "")
        self.event = str(payload.get("event") or "")
        data = payload.get("data") or {}
        key = data.get("key") or {}
        self.from_me = bool(key.get("fromMe", False))
        self.remote_jid = str(key.get("remoteJid") or "")
        self.push_name = str(data.get("pushName") or "")
        self.message = data.get("message") or {}
        self.message_type = str(data.get("messageType") or "")

    @property
    def sender_phone(self) -> str:
        """Strip @s.whatsapp.net / @lid and any :<id> suffix from the remote JID."""
        if not self.remote_jid:
            return ""
        return self.remote_jid.split("@")[0].split(":")[0]

    @property
    def text(self) -> str:
        msg = self.message
        conversation = msg.get("conversation")
        if conversation:
            return str(conversation)
        ext = msg.get("extendedTextMessage") or {}
        if ext.get("text"):
            return str(ext["text"])
        return "[非文字消息]"

    def format_for_feishu(self) -> str:
        """Same readable format the old n8n workflow produced.

        Keeps WA_NUMBER / INSTANCE lines so a future Feishu -> WA reply handler
        (like the old n8n v2 workflow) can parse them.
        """
        now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        sender = self.push_name or self.sender_phone
        return (
            f"于{now}收到了一条来自{sender}的消息，请及时查看！\n"
            f"消息内容为：\n{self.text}\n\n"
            f"WA_NUMBER:{self.sender_phone}\nINSTANCE:{self.instance}"
        )

    @property
    def is_group(self) -> bool:
        """True for group (@g.us) or broadcast (@broadcast) messages."""
        return self.remote_jid.endswith("@g.us") or self.remote_jid.endswith("@broadcast")

    @property
    def media_type(self) -> str:
        """Media kind: image | document | audio | video | sticker | none."""
        for kind in ("imageMessage", "documentMessage", "audioMessage", "videoMessage", "stickerMessage"):
            if kind in self.message:
                return kind.replace("Message", "").lower()
        return "none"

    @property
    def media_note(self) -> str:
        return {
            "image": "[图片消息]",
            "document": "[文件消息]",
            "audio": "[语音消息]",
            "video": "[视频消息]",
            "sticker": "[表情消息]",
        }.get(self.media_type, "[非文字消息]")


class EvolutionClient:
    """Minimal Evolution API client (sendText + media download)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        resp = httpx.post(url, json=body, headers=headers, timeout=120)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("status") == 400):
            raise RuntimeError(f"Evolution error: status={resp.status_code} body={data}")
        return data

    def send_text(self, instance: str, number: str, text: str) -> dict:
        data = self._post(f"/message/sendText/{quote(instance, safe='')}", {"number": number, "text": text})
        logger.info("sent WhatsApp reply to %s via instance %s", number, instance)
        return data

    def send_media(
        self,
        instance: str,
        number: str,
        mediatype: str,
        media: str,
        caption: str | None = None,
        file_name: str | None = None,
    ) -> dict:
        """Send media (image/document/audio/video). `media` is a URL or a data URI
        (e.g. data:image/jpeg;base64,...). fileName is required for base64 documents."""
        body = {"number": number, "mediatype": mediatype, "media": media}
        if caption:
            body["caption"] = caption
        if file_name:
            body["fileName"] = file_name
        data = self._post(f"/message/sendMedia/{quote(instance, safe='')}", body)
        logger.info("sent WhatsApp %s to %s via instance %s", mediatype, number, instance)
        return data

    def get_media_base64(self, instance: str, message_obj: dict) -> dict:
        """Download a media message as base64. message_obj = the full webhook data object
        (key + message + messageType + ...), as required by Evolution's
        /chat/getBase64FromMediaMessage."""
        data = self._post(
            f"/chat/getBase64FromMediaMessage/{quote(instance, safe='')}",
            {"message": message_obj},
        )
        return data  # e.g. {"base64": "...", "mimetype": "image/jpeg", "length": 123, "fileName": "..."}
