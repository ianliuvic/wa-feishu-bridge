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


class EvolutionClient:
    """Minimal Evolution API client (sendText)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def send_text(self, instance: str, number: str, text: str) -> dict:
        url = f"{self.base_url}/message/sendText/{quote(instance, safe='')}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        body = {"number": number, "text": text}
        resp = httpx.post(url, json=body, headers=headers, timeout=30)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if resp.status_code >= 400 or data.get("status") == 400:
            raise RuntimeError(f"Evolution send error: status={resp.status_code} body={data}")
        logger.info("sent WhatsApp reply to %s via instance %s", number, instance)
        return data
