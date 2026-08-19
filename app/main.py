"""Evolution -> Feishu bridge.

- Forwards WhatsApp messages to a Feishu group as styled interactive cards
  (text / image / document, with metadata; oversized media -> placeholder).
- Off-hours auto-reply (00:00-08:00 Asia/Shanghai): once per customer per window,
  DeepSeek replies in the customer's language via Evolution.
- Feishu -> WhatsApp reply: members replying to a forwarded card reach the
  original WhatsApp customer (text and image replies supported).
"""

import base64
import json
import logging
import re
from collections import deque

import httpx
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
    FEISHU_EVENT_FORWARD_URL,
    FEISHU_EVENT_PATH,
    FEISHU_REPLY_CONFIRM,
)
from .autoreply import AutoReplyManager
from .evolution import EvolutionClient, EvolutionMessage
from .feishu import FeishuClient
from .llm import DeepSeekClient
from .replymap import ReplyMap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI(title="WA-Feishu Bridge", version="1.4.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
reply_map = ReplyMap()

_llm: DeepSeekClient | None = None
_evolution: EvolutionClient | None = None
_autoreply: AutoReplyManager | None = None

# Feishu delivers events at-least-once (and this URL is registered for both
# 事件配置 and 回调配置), so dedupe by event_id and by reply message_id.
_seen_event_ids: deque[str] = deque(maxlen=500)
_seen_event_id_set: set[str] = set()
_seen_msg_ids: deque[str] = deque(maxlen=500)
_seen_msg_id_set: set[str] = set()


def _seen(key: str, queue: deque, seen_set: set) -> bool:
    if key in seen_set:
        return True
    if len(queue) >= queue.maxlen:
        seen_set.discard(queue[0])  # about to be dropped by maxlen
    seen_set.add(key)
    queue.append(key)
    return False


def _strip_mention(text: str) -> str:
    """Remove Feishu bot @mentions (e.g. '@_user_1 ') inserted when replying to a card."""
    return re.sub(r"@\S+\s?", "", text).strip()


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


def _send_and_map(card: dict, evt: EvolutionMessage) -> None:
    """Send a card and register message_id -> customer so replies can be routed back."""
    result = feishu.send_card(FEISHU_CHAT_ID, card)
    msg_id = (result.get("data") or {}).get("message_id")
    if msg_id:
        reply_map.register(msg_id, evt.remote_jid, evt.sender_phone, evt.instance, evt.push_name)


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
                _send_and_map(cards.placeholder_card(evt, note), evt)
                return
            data, mimetype, file_name = _decode_base64(media)
            if kind == "image":
                img_key = feishu.upload_image(data, mimetype)
                _send_and_map(cards.image_card(evt, img_key), evt)
            else:
                doc = evt.message.get("documentMessage") or {}
                file_name = doc.get("fileName") or file_name
                file_key = feishu.upload_file(data, file_name, mimetype)
                try:
                    _send_and_map(cards.file_card(evt, file_key, file_name), evt)
                except RuntimeError as exc:
                    # file element rejected -> fall back to a plain file message
                    # plus a metadata card (sender/number/instance/time)
                    logger.warning("file card rejected (%s), sending file message instead", exc)
                    result = feishu.send_file_message(FEISHU_CHAT_ID, file_key, file_name)
                    msg_id = (result.get("data") or {}).get("message_id")
                    if msg_id:
                        reply_map.register(msg_id, evt.remote_jid, evt.sender_phone, evt.instance, evt.push_name)
                    _send_and_map(cards.placeholder_card(evt, f"[文件消息] {file_name}"), evt)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s forward failed (%s), falling back to placeholder", kind, exc)

    if kind == "none":
        _send_and_map(cards.text_card(evt), evt)
    else:
        _send_and_map(cards.placeholder_card(evt, evt.media_note), evt)


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
        "reply": {
            "enabled": True,
            "callback": FEISHU_EVENT_PATH,
            "mapped_messages": reply_map.size(),
        },
    }


@app.post(FEISHU_EVENT_PATH)
async def feishu_event(request: Request):
    """Feishu event subscription callback (single URL per app).

    - url_verification: answer the challenge (saving the callback in the console).
    - Every other event is proxied to FEISHU_EVENT_FORWARD_URL so an existing
      consumer (e.g. mail-poller's card.action.trigger) keeps working.
    - im.message.receive_v1 is additionally handled locally: a reply to a
      forwarded card is sent back to the original WhatsApp customer.
    """
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("bad feishu event json: %s", exc)
        return {"status": "error", "reason": "invalid json"}

    # Feishu URL verification when saving the callback in the admin console.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event_type = (payload.get("header") or {}).get("event_type")

    # At-least-once delivery: skip duplicate events (first one already forwarded).
    event_id = (payload.get("header") or {}).get("event_id", "")
    if event_id and _seen(event_id, _seen_event_ids, _seen_event_id_set):
        logger.info("duplicate feishu event_id=%s, skipping", event_id[:24])
        return {"code": 0}

    # Proxy everything to the previous callback holder (mail-poller) unchanged.
    forward_resp: dict = {"code": 0}
    if FEISHU_EVENT_FORWARD_URL:
        try:
            fwd = httpx.post(FEISHU_EVENT_FORWARD_URL, json=payload, timeout=10)
            if (
                fwd.status_code < 400
                and fwd.headers.get("content-type", "").startswith("application/json")
            ):
                forward_resp = fwd.json()
            else:
                logger.warning("feishu event forward returned HTTP %s", fwd.status_code)
                forward_resp = {"code": 1}
        except Exception as exc:  # noqa: BLE001
            logger.warning("feishu event forward to %s failed: %s", FEISHU_EVENT_FORWARD_URL, exc)
            forward_resp = {"code": 1}

    if event_type == "im.message.receive_v1":
        await _handle_receive(payload)

    return forward_resp


async def _handle_receive(payload: dict) -> None:
    """Handle im.message.receive_v1: route a reply to a forwarded card to WhatsApp."""
    event = payload.get("event") or {}
    msg = event.get("message") or {}
    parent_id = msg.get("parent_id")
    chat_id = msg.get("chat_id")
    if chat_id != FEISHU_CHAT_ID:
        return
    if not parent_id:
        return
    sender = event.get("sender") or {}
    if sender.get("sender_type") != "user":
        return

    # Dedupe: the same reply may be delivered twice (dual channel / at-least-once).
    msg_id = msg.get("message_id", "")
    if msg_id and _seen(msg_id, _seen_msg_ids, _seen_msg_id_set):
        logger.info("duplicate reply message_id=%s, skipping", msg_id[:24])
        return

    target = reply_map.get(parent_id)
    if target is None:
        return

    message_type = msg.get("message_type")
    try:
        content = json.loads(msg.get("content") or "{}")
        if message_type == "text":
            text = _strip_mention(content.get("text") or "")
            if not text:
                return
            get_evolution().send_text(target.instance, target.phone, text)
            preview = text[:60]
        elif message_type == "image":
            image_key = content.get("image_key")
            if not image_key:
                return
            data, mimetype, _ = feishu.download_resource(msg.get("message_id"), image_key, "image")
            media_uri = f"data:{mimetype};base64,{base64.b64encode(data).decode('ascii')}"
            get_evolution().send_media(target.instance, target.phone, "image", media_uri)
            preview = "[图片]"
        elif message_type == "file":
            file_key = content.get("file_key")
            if not file_key:
                return
            data, mimetype, file_name = feishu.download_resource(msg.get("message_id"), file_key, "file")
            media_uri = f"data:{mimetype};base64,{base64.b64encode(data).decode('ascii')}"
            get_evolution().send_media(
                target.instance, target.phone, "document", media_uri, file_name=file_name
            )
            preview = f"[文件] {file_name}"
        else:
            logger.info("unsupported reply type=%s", message_type)
            return
    except Exception as exc:  # noqa: BLE001
        logger.exception("feishu reply failed")
        try:
            feishu.send_card(FEISHU_CHAT_ID, cards.error_card(str(exc)[:120]))
        except Exception:  # noqa: BLE001
            pass
        return

    logger.info("replied to customer %s via %s", target.remote_jid, target.instance)
    if FEISHU_REPLY_CONFIRM:
        try:
            customer = f"{target.push_name}（{target.phone}）" if target.push_name else target.phone
            feishu.send_card(FEISHU_CHAT_ID, cards.confirm_card(customer, preview))
        except Exception:  # noqa: BLE001
            logger.exception("confirm card failed")


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
