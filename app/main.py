"""Evolution -> Feishu bridge.

Receives Evolution API webhooks (event: messages.upsert), drops own messages
(fromMe=true), formats the incoming WhatsApp message and forwards it to a Feishu
group chat using the configured Feishu bot.

Future extension point: call an LLM / RAG here and reply via Evolution's
/message/sendText using EVOLUTION_BASE_URL / EVOLUTION_API_KEY.
"""

import logging

from fastapi import FastAPI, Request

from .config import (
    AUTO_REPLY_ENABLED,
    AUTO_REPLY_END_HOUR,
    AUTO_REPLY_START_HOUR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    EVOLUTION_API_KEY,
    EVOLUTION_BASE_URL,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_CHAT_ID,
)
from .autoreply import AutoReplyManager
from .evolution import EvolutionClient, EvolutionMessage
from .feishu import FeishuClient
from .llm import DeepSeekClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI(title="WA-Feishu Bridge", version="1.2.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)

_llm: DeepSeekClient | None = None
_autoreply: AutoReplyManager | None = None


def get_llm() -> DeepSeekClient | None:
    """Lazily build the DeepSeek client. Returns None when no API key is set."""
    global _llm
    if _llm is None and DEEPSEEK_API_KEY:
        _llm = DeepSeekClient(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)
    return _llm


def get_autoreply() -> AutoReplyManager | None:
    """Lazily build the auto-reply manager when all prerequisites are present."""
    global _autoreply
    if _autoreply is None and AUTO_REPLY_ENABLED and DEEPSEEK_API_KEY and EVOLUTION_BASE_URL and EVOLUTION_API_KEY:
        _autoreply = AutoReplyManager(
            llm=get_llm(),
            evolution=EvolutionClient(EVOLUTION_BASE_URL, EVOLUTION_API_KEY),
            start_hour=AUTO_REPLY_START_HOUR,
            end_hour=AUTO_REPLY_END_HOUR,
        )
        logger.info(
            "auto-reply enabled: %02d:00-%02d:00 Asia/Shanghai, model=%s",
            AUTO_REPLY_START_HOUR,
            AUTO_REPLY_END_HOUR,
            DEEPSEEK_MODEL,
        )
    return _autoreply


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "chat_id": FEISHU_CHAT_ID,
        "llm": {
            "configured": bool(DEEPSEEK_API_KEY),
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
        },
        "auto_reply": {
            "enabled": get_autoreply() is not None,
            "window": f"{AUTO_REPLY_START_HOUR:02d}:00-{AUTO_REPLY_END_HOUR:02d}:00 Asia/Shanghai",
        },
    }


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("bad json body: %s", exc)
        return {"status": "error", "reason": "invalid json"}

    evt = EvolutionMessage(payload)

    if evt.event != "messages.upsert":
        logger.info("skipping event=%s", evt.event)
        return {"status": "skipped", "reason": f"event={evt.event}"}

    if evt.from_me:
        logger.info("skipping own message from %s", evt.remote_jid)
        return {"status": "skipped", "reason": "fromMe=true"}

    text = evt.format_for_feishu()
    logger.info(
        "forwarding instance=%s from=%s type=%s text=%s",
        evt.instance, evt.sender_phone, evt.message_type, evt.text[:80],
    )
    try:
        feishu.send_text(FEISHU_CHAT_ID, text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("feishu send failed")
        return {"status": "error", "reason": str(exc)}

    # Off-hours auto-reply (once per customer per window). Independent of the
    # Feishu forwarding above; failures are logged, never block the webhook.
    manager = get_autoreply()
    if manager is not None:
        try:
            reply = manager.maybe_reply(evt)
            if reply:
                logger.info("auto-reply sent: %s", reply[:80])
        except Exception:  # noqa: BLE001
            logger.exception("auto-reply failed")

    return {"status": "ok"}
