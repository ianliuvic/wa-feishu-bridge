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
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_CHAT_ID,
)
from .evolution import EvolutionMessage
from .feishu import FeishuClient
from .llm import DeepSeekClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI(title="WA-Feishu Bridge", version="1.1.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)

_llm: DeepSeekClient | None = None


def get_llm() -> DeepSeekClient | None:
    """Lazily build the DeepSeek client. Returns None when no API key is set."""
    global _llm
    if _llm is None and DEEPSEEK_API_KEY:
        _llm = DeepSeekClient(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)
    return _llm


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
    return {"status": "ok"}
