"""Parsing and formatting of Evolution API webhook payloads."""

from datetime import datetime, timedelta, timezone

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
