"""Evolution -> Feishu bridge.

Receives Evolution API webhooks (event: messages.upsert), drops own messages
(fromMe=true), formats the incoming WhatsApp message and forwards it to a Feishu
group chat using the configured Feishu bot.

Future extension point: call an LLM / RAG here and reply via Evolution's
/message/sendText using EVOLUTION_BASE_URL / EVOLUTION_API_KEY.
"""

import logging

from fastapi import FastAPI, Request

from .config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID
from .evolution import EvolutionMessage
from .feishu import FeishuClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI(title="WA-Feishu Bridge", version="1.0.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)


@app.get("/health")
async def health():
    return {"status": "ok", "chat_id": FEISHU_CHAT_ID}


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
