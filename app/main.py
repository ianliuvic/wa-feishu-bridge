"""Evolution -> Feishu bridge.

Receives Evolution API webhooks (event: messages.upsert), drops own messages
(fromMe=true), forwards the incoming WhatsApp message to a Feishu group chat as
a styled interactive card (text / image / document, with metadata), and during
off-hours (00:00-08:00 Asia/Shanghai) auto-replies once per customer per window
via DeepSeek in the customer's language.
"""

import base64
import logging

from fastapi import FastAPI, Request

from . import cards
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

app = FastAPI(title="WA-Feishu Bridge", version="1.3.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)

_llm: DeepSeekClient | None = None
_evolution: EvolutionClient | None = None
_autoreply: AutoReplyManager | None = None


def get_llm() -> DeepSeekClient | None:
    """Lazily build the DeepSeek client. Returns None when no API key is set."""
    global _llm
    if _llm is None and DEEPSEEK_API_KEY:
        _llm = DeepSeekClient(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)
    return _llm


def get_evolution() -> EvolutionClient | None:
    """Lazily build the Evolution client (used for media download + auto-reply)."""
    global _evolution
    if _evolution is None and EVOLUTION_BASE_URL and EVOLUTION_API_KEY:
        _evolution = EvolutionClient(EVOLUTION_BASE_URL, EVOLUTION_API_KEY)
    return _evolution


def get_autoreply() -> AutoReplyManager | None:
    """Lazily build the auto-reply manager when all prerequisites are present."""
    global _autoreply
    if _autoreply is None and AUTO_REPLY_ENABLED and DEEPSEEK_API_KEY and EVOLUTION_BASE_URL and EVOLUTION_API_KEY:
        _autoreply = AutoReplyManager(
            llm=get_llm(),
            evolution=get_evolution(),
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


IMAGE_MAX_BYTES = 9 * 1024 * 1024      # Feishu image upload limit is 10MB
DOC_MAX_BYTES = 28 * 1024 * 1024       # Feishu file upload limit is 30MB


def _media_size(media: dict) -> int:
    """Numeric size in bytes from Evolution's media response.

    For documents 'size' is an int; for images it is a dict holding
    fileLength (uint32 low/high) plus width/height metadata.
    """
    size = media.get("size")
    if isinstance(size, int):
        return size
    if isinstance(size, dict):
        fl = size.get("fileLength")
        if isinstance(fl, dict):
            low = fl.get("low") or 0
            high = fl.get("high") or 0
            return low + (int(high) << 32)
        if isinstance(fl, int):
            return fl
    return 0


def _decode_base64(media: dict) -> tuple[bytes, str, str]:
    """Return (bytes, mimetype, filename) from Evolution's media response."""
    raw = media.get("base64") or ""
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    data = base64.b64decode(raw)
    mimetype = media.get("mimetype") or "application/octet-stream"
    file_name = media.get("fileName") or media.get("filename") or "media"
    return data, mimetype, file_name


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    return f"{n / 1024:.0f}KB"


def forward_to_feishu(evt: EvolutionMessage) -> None:
    """Forward a message to the Feishu group as a styled card (with media when possible)."""
    kind = evt.media_type
    evolution = get_evolution()

    if kind in ("image", "document") and evolution is not None:
        try:
            media = evolution.get_media_base64(evt.instance, evt.payload.get("data") or {})
            size = _media_size(media)
            limit = IMAGE_MAX_BYTES if kind == "image" else DOC_MAX_BYTES
            if size and size > limit:
                logger.warning("%s too large (%s), sending placeholder", kind, _fmt_size(size))
                note = f"{evt.media_note}（文件过大 {_fmt_size(size)}，未转发）"
                feishu.send_card(FEISHU_CHAT_ID, cards.placeholder_card(evt, note))
                return
            data, mimetype, file_name = _decode_base64(media)
            if kind == "image":
                img_key = feishu.upload_image(data, mimetype)
                feishu.send_card(FEISHU_CHAT_ID, cards.image_card(evt, img_key))
            else:
                doc = evt.message.get("documentMessage") or {}
                file_name = doc.get("fileName") or file_name
                file_key = feishu.upload_file(data, file_name, mimetype)
                try:
                    feishu.send_card(FEISHU_CHAT_ID, cards.file_card(evt, file_key, file_name))
                except RuntimeError as exc:
                    # file element rejected -> fall back to a plain file message
                    # plus a metadata card (sender/number/instance/time)
                    logger.warning("file card rejected (%s), sending file message instead", exc)
                    feishu.send_file_message(FEISHU_CHAT_ID, file_key, file_name)
                    feishu.send_card(FEISHU_CHAT_ID, cards.placeholder_card(evt, f"[文件消息] {file_name}"))
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s forward failed (%s), falling back to placeholder", kind, exc)

    if kind == "none":
        feishu.send_card(FEISHU_CHAT_ID, cards.text_card(evt))
    else:
        feishu.send_card(FEISHU_CHAT_ID, cards.placeholder_card(evt, evt.media_note))


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

    logger.info(
        "forwarding instance=%s from=%s type=%s media=%s text=%s",
        evt.instance, evt.sender_phone, evt.message_type, evt.media_type, evt.text[:60],
    )
    try:
        forward_to_feishu(evt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("feishu forward failed")
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
