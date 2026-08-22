"""Evolution -> Feishu bridge.

- Forwards WhatsApp messages to a Feishu group as styled interactive cards
  (text / image / document, with metadata; oversized media -> placeholder).
- Off-hours auto-reply (00:00-08:00 Asia/Shanghai): once per customer per window,
  DeepSeek replies in the customer's language via Evolution.
- Feishu -> WhatsApp reply: members replying to a forwarded card reach the
  original WhatsApp customer (text and image replies supported).
"""

import asyncio
import base64
import hmac
import json
import logging
import re
import time
from collections import deque

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import cards
from .config import (
    AUTO_REPLY_ENABLED,
    AUTO_REPLY_END_HOUR,
    AUTO_REPLY_START_HOUR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    CODEX_RUN_TIMEOUT_SECONDS,
    CODEX_WORKER_TOKEN,
    CODEX_WORKER_URL,
    EVOLUTION_API_KEY,
    EVOLUTION_BASE_URL,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_CHAT_ID,
    FEISHU_EVENT_FORWARD_URL,
    FEISHU_EVENT_PATH,
    FEISHU_REPLY_CONFIRM,
    MARKETING_CHAT_ID,
    SCHEDULER_API_TOKEN,
    SCHEDULER_DB_PATH,
    SCHEDULER_DEFAULT_TIMEZONE,
    SCHEDULER_POLL_SECONDS,
)
from .autoreply import AutoReplyManager
from .evolution import EvolutionClient, EvolutionMessage
from .feishu import FeishuClient
from .llm import DeepSeekClient
from .replymap import ReplyMap
from .scheduler import SchedulerStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI(title="WA-Feishu Bridge", version="2.0.0")
feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
reply_map = ReplyMap()
scheduler_store = SchedulerStore(SCHEDULER_DB_PATH)

_llm: DeepSeekClient | None = None
_evolution: EvolutionClient | None = None
_autoreply: AutoReplyManager | None = None
_scheduler_loop_task: asyncio.Task | None = None

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


class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=200_000)
    cron: str = Field(min_length=5, max_length=200)
    timezone: str = Field(default=SCHEDULER_DEFAULT_TIMEZONE, min_length=1, max_length=100)
    chat_id: str | None = None


def require_scheduler_auth(authorization: str | None = Header(default=None)) -> None:
    if not SCHEDULER_API_TOKEN:
        raise HTTPException(status_code=503, detail="SCHEDULER_API_TOKEN is not configured")
    prefix = "Bearer "
    supplied = authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    if not hmac.compare_digest(supplied, SCHEDULER_API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")


async def call_codex(prompt: str, session_id: str | None = None) -> dict:
    if not CODEX_WORKER_URL or not CODEX_WORKER_TOKEN:
        raise RuntimeError("Codex worker is not configured")
    headers = {"Authorization": f"Bearer {CODEX_WORKER_TOKEN}"}
    body = {"prompt": prompt, "session_id": session_id, "workspace": "/workspace"}
    timeout = httpx.Timeout(CODEX_RUN_TIMEOUT_SECONDS + 30, connect=15)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{CODEX_WORKER_URL}/v1/runs", json=body, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"Codex worker HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


def _marketing_prompt(text: str, chat_id: str) -> str:
    return f"""你正在通过飞书 marketing 群与用户对话。
直接回答用户，不要复述本说明。需要创建、修改、暂停、恢复、立即运行或删除定时营销任务时，必须使用 marketing-scheduler skill；创建任务前确认时间、时区和任务内容。
当前飞书 chat_id: {chat_id}

用户消息：
{text}
"""


async def process_marketing_message(chat_id: str, message_id: str, text: str) -> None:
    command = text.strip()
    progress_message_id: str | None = None
    progress_stop: asyncio.Event | None = None
    progress_task: asyncio.Task | None = None

    async def update_progress(status: str) -> bool:
        if not progress_message_id:
            return False
        try:
            await asyncio.to_thread(feishu.update_text, progress_message_id, status)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("failed to update Codex progress message")
            return False

    async def progress_heartbeat(stop: asyncio.Event) -> None:
        started_at = time.monotonic()
        interval = 8
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                elapsed = max(1, int(time.monotonic() - started_at))
                await update_progress(f"⚙️ Codex 正在执行…（已用时 {elapsed} 秒）")
                interval = 15

    try:
        if command == "/new" or command.startswith("/new "):
            scheduler_store.clear_session(chat_id)
            await asyncio.to_thread(feishu.reply_text, message_id, "已开启新的 Codex 会话。")
            return
        if command == "/session":
            current = scheduler_store.get_session(chat_id)
            message = f"当前 Codex 会话：{current}" if current else "当前尚未建立 Codex 会话。"
            await asyncio.to_thread(feishu.reply_text, message_id, message)
            return

        progress_result = await asyncio.to_thread(
            feishu.reply_text, message_id, "🧠 Codex 正在分析你的请求…"
        )
        progress_message_id = (progress_result.get("data") or {}).get("message_id")
        progress_stop = asyncio.Event()
        progress_task = asyncio.create_task(progress_heartbeat(progress_stop))

        session_id = scheduler_store.get_session(chat_id)
        result = await call_codex(_marketing_prompt(command, chat_id), session_id)
        returned_session = result.get("session_id")
        if returned_session:
            scheduler_store.set_session(chat_id, returned_session)
        answer = (result.get("response") or "Codex 未返回文本结果。").strip()
        progress_stop.set()
        await progress_task
        if not await update_progress(answer[:28000]):
            await asyncio.to_thread(feishu.reply_text, message_id, answer[:28000])
    except Exception as exc:  # noqa: BLE001
        logger.exception("marketing Codex request failed")
        if progress_stop:
            progress_stop.set()
        if progress_task:
            await progress_task
        error_message = f"❌ Codex 执行失败：{str(exc)[:500]}"
        if not await update_progress(error_message):
            await asyncio.to_thread(feishu.reply_text, message_id, error_message)


async def run_scheduled_task(task, run_id: str) -> None:
    prompt = f"""这是持久化定时营销任务的一次正式执行，不是在创建新的计划任务。
任务名称：{task.name}
任务时区：{task.timezone}
请现在完成以下任务，并给出适合直接发送到飞书审核的最终结果。可按需使用已安装的 skills。

{task.prompt}
"""
    try:
        result = await call_codex(prompt)
        answer = (result.get("response") or "Codex 未返回文本结果。").strip()
        await asyncio.to_thread(
            feishu.send_text, task.chat_id, f"【定时任务：{task.name}】\n{answer[:27500]}"
        )
        scheduler_store.finish_run(task.id, run_id, response=answer)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduled task %s failed", task.id)
        scheduler_store.finish_run(task.id, run_id, error=str(exc))
        try:
            await asyncio.to_thread(
                feishu.send_text,
                task.chat_id,
                f"【定时任务失败：{task.name}】\n{str(exc)[:800]}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to notify Feishu about scheduled task error")


async def scheduler_loop() -> None:
    while True:
        try:
            due = await asyncio.to_thread(scheduler_store.claim_due)
            for task, run_id in due:
                asyncio.create_task(run_scheduled_task(task, run_id))
        except Exception:  # noqa: BLE001
            logger.exception("scheduler polling failed")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


@app.on_event("startup")
async def start_scheduler() -> None:
    global _scheduler_loop_task
    _scheduler_loop_task = asyncio.create_task(scheduler_loop())


@app.on_event("shutdown")
async def stop_scheduler() -> None:
    if _scheduler_loop_task:
        _scheduler_loop_task.cancel()
        try:
            await _scheduler_loop_task
        except asyncio.CancelledError:
            pass


async def forward_feishu_payload(payload: dict) -> dict:
    if not FEISHU_EVENT_FORWARD_URL:
        return {"code": 0}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(FEISHU_EVENT_FORWARD_URL, json=payload)
        if response.status_code < 400 and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            return response.json()
        logger.warning("feishu event forward returned HTTP %s", response.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("feishu event forward to %s failed: %s", FEISHU_EVENT_FORWARD_URL, exc)
    return {"code": 1}


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
        "codex": {
            "configured": bool(CODEX_WORKER_URL and CODEX_WORKER_TOKEN),
            "marketing_chat_configured": bool(MARKETING_CHAT_ID),
        },
        "scheduler": scheduler_store.health(),
    }


@app.get("/api/scheduler/tasks", dependencies=[Depends(require_scheduler_auth)])
async def list_scheduled_tasks():
    return {"tasks": [task.as_dict() for task in scheduler_store.list_tasks()]}


@app.get("/api/codex/health", dependencies=[Depends(require_scheduler_auth)])
async def codex_worker_health():
    if not CODEX_WORKER_URL:
        raise HTTPException(status_code=503, detail="CODEX_WORKER_URL is not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{CODEX_WORKER_URL}/health")
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Codex worker unavailable: {exc}") from exc


@app.post("/api/scheduler/tasks", dependencies=[Depends(require_scheduler_auth)])
async def create_scheduled_task(body: ScheduledTaskCreate):
    chat_id = (body.chat_id or MARKETING_CHAT_ID).strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    try:
        task = scheduler_store.create_task(
            name=body.name.strip(),
            prompt=body.prompt.strip(),
            cron=body.cron.strip(),
            timezone_name=body.timezone.strip(),
            chat_id=chat_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task.as_dict()


def _task_or_404(task_id: str):
    try:
        return scheduler_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.post("/api/scheduler/tasks/{task_id}/pause", dependencies=[Depends(require_scheduler_auth)])
async def pause_scheduled_task(task_id: str):
    _task_or_404(task_id)
    return scheduler_store.set_enabled(task_id, False).as_dict()


@app.post("/api/scheduler/tasks/{task_id}/resume", dependencies=[Depends(require_scheduler_auth)])
async def resume_scheduled_task(task_id: str):
    _task_or_404(task_id)
    return scheduler_store.set_enabled(task_id, True).as_dict()


@app.post("/api/scheduler/tasks/{task_id}/run", dependencies=[Depends(require_scheduler_auth)])
async def run_scheduled_task_now(task_id: str):
    _task_or_404(task_id)
    return scheduler_store.run_now(task_id).as_dict()


@app.delete("/api/scheduler/tasks/{task_id}", dependencies=[Depends(require_scheduler_auth)])
async def delete_scheduled_task(task_id: str):
    _task_or_404(task_id)
    scheduler_store.delete_task(task_id)
    return {"deleted": True, "id": task_id}


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

    if event_type == "im.message.receive_v1":
        await _handle_receive(payload)
        # Message events do not need a synchronous response. Acknowledge Feishu
        # immediately and keep the existing mail consumer updated in background.
        if FEISHU_EVENT_FORWARD_URL:
            asyncio.create_task(forward_feishu_payload(payload))
        return {"code": 0}

    # Card callbacks may need their downstream response body, so preserve the
    # original synchronous proxy behavior for non-message events.
    return await forward_feishu_payload(payload)


async def _handle_receive(payload: dict) -> None:
    """Route marketing chat to Codex and card replies to WhatsApp."""
    event = payload.get("event") or {}
    msg = event.get("message") or {}
    parent_id = msg.get("parent_id")
    chat_id = msg.get("chat_id")
    sender = event.get("sender") or {}
    if sender.get("sender_type") != "user":
        return

    if MARKETING_CHAT_ID and chat_id == MARKETING_CHAT_ID:
        if msg.get("message_type") != "text":
            await asyncio.to_thread(
                feishu.reply_text,
                msg.get("message_id", ""),
                "目前 Codex 对话入口先支持文本消息。",
            )
            return
        try:
            content = json.loads(msg.get("content") or "{}")
        except json.JSONDecodeError:
            return
        text = _strip_mention(content.get("text") or "")
        message_id = msg.get("message_id", "")
        if text and message_id:
            asyncio.create_task(process_marketing_message(chat_id, message_id, text))
        return

    if chat_id != FEISHU_CHAT_ID or not parent_id:
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
