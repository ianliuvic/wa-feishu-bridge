"""Configuration from environment variables."""

import os


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


FEISHU_API_BASE = os.getenv("FEISHU_API_BASE", "https://open.feishu.cn")
FEISHU_APP_ID = _require("FEISHU_APP_ID")
FEISHU_APP_SECRET = _require("FEISHU_APP_SECRET")
FEISHU_CHAT_ID = _require("FEISHU_CHAT_ID")

# Optional: base URL of the Evolution API, needed later for replying to WhatsApp
# (e.g. LLM auto-reply). Not used by the forward-only bridge yet.
EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

# Optional: DeepSeek (OpenAI-compatible) LLM capability. Not used by the
# forwarding flow yet; available for summarization / auto-reply features.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Off-hours auto-reply (Asia/Shanghai). Only active when AUTO_REPLY_ENABLED and
# DeepSeek + Evolution credentials are all configured.
AUTO_REPLY_ENABLED = os.getenv("AUTO_REPLY_ENABLED", "true").lower() in ("1", "true", "yes", "on")
AUTO_REPLY_START_HOUR = int(os.getenv("AUTO_REPLY_START_HOUR", "0"))
AUTO_REPLY_END_HOUR = int(os.getenv("AUTO_REPLY_END_HOUR", "8"))

# Feishu card styling (header template/color, title, footer hint).
FEISHU_CARD_TEMPLATE = os.getenv("FEISHU_CARD_TEMPLATE", "blue")
FEISHU_CARD_TITLE = os.getenv("FEISHU_CARD_TITLE", "WhatsApp 新消息")
FEISHU_CARD_FOOTER = os.getenv("FEISHU_CARD_FOOTER", "💬 回复此消息可回复客户")

# Feishu -> WhatsApp reply feature: confirm card after replying.
FEISHU_REPLY_CONFIRM = os.getenv("FEISHU_REPLY_CONFIRM", "true").lower() in ("1", "true", "yes", "on")

# Feishu event subscription (im.message.receive_v1) callback path.
FEISHU_EVENT_PATH = os.getenv("FEISHU_EVENT_PATH", "/webhook/feishu")

# Forward all other Feishu events (e.g. card.action.trigger) to this URL so an
# existing consumer keeps working. Feishu allows only ONE callback per app, so
# this bridge acts as the single entry and proxies the rest.
FEISHU_EVENT_FORWARD_URL = os.getenv("FEISHU_EVENT_FORWARD_URL", "")

# Feishu marketing group -> Codex bridge. The WhatsApp target FEISHU_CHAT_ID stays
# separate so the existing customer reply flow is not affected.
MARKETING_CHAT_ID = os.getenv("MARKETING_CHAT_ID", "").strip()
# Interactive marketing-group chat: a message in MARKETING_CHAT_ID is handed to
# the codex executor, and images/files are staged for it. Turning this off stops
# both, and the group keeps receiving scheduled task output as before.
#
# Opt-out, and a blank value means ON: an environment entry that exists but is
# empty must not silently switch a live feature off.
_MARKETING_CHAT_VALUE = os.getenv("MARKETING_CHAT_ENABLED", "").strip().lower()
MARKETING_CHAT_ENABLED = (
    True if not _MARKETING_CHAT_VALUE
    else _MARKETING_CHAT_VALUE in ("1", "true", "yes", "on")
)
CODEX_WORKER_URL = os.getenv("CODEX_WORKER_URL", "").rstrip("/")
CODEX_WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "")
CODEX_RUN_TIMEOUT_SECONDS = int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800"))

# Executor routing. Every scheduled task names the executor it runs on, and the
# default keeps existing tasks on codex-worker so adding the column changes no
# behaviour. The DSH worker is opt-in per task.
DSH_WORKER_URL = os.getenv("DSH_WORKER_URL", "").rstrip("/")
DSH_WORKER_TOKEN = os.getenv("DSH_WORKER_TOKEN", "")
DEFAULT_WORKER = (os.getenv("DEFAULT_WORKER", "codex").strip() or "codex").lower()
WORKER_NAMES = ("codex", "dsh")


def worker_endpoint(name: str | None = None) -> tuple[str, str, str]:
    """Resolve an executor name to (name, base_url, token).

    @param name - executor from the task row; None/blank uses DEFAULT_WORKER.
    @returns the normalized name, its base URL, and its bearer token.
    @throws RuntimeError when the name is unknown or unconfigured, so a
        misconfigured task fails loudly instead of silently falling back.
    """
    key = (name or DEFAULT_WORKER).strip().lower() or DEFAULT_WORKER
    if key == "codex":
        url, token = CODEX_WORKER_URL, CODEX_WORKER_TOKEN
    elif key == "dsh":
        url, token = DSH_WORKER_URL, DSH_WORKER_TOKEN
    else:
        raise RuntimeError(f"unknown executor {key!r}; expected one of {WORKER_NAMES}")
    if not url or not token:
        raise RuntimeError(f"executor {key!r} is not configured")
    return key, url, token

BRIDGE_PUBLIC_URL = os.getenv("BRIDGE_PUBLIC_URL", "https://wa-bridge.yiswim.cloud").rstrip("/")
ATTACHMENT_DIR = os.getenv("ATTACHMENT_DIR", "/data/pending-attachments")
ATTACHMENT_TTL_SECONDS = max(30, int(os.getenv("ATTACHMENT_TTL_SECONDS", "120")))
ATTACHMENT_MAX_FILES = max(1, int(os.getenv("ATTACHMENT_MAX_FILES", "10")))
ATTACHMENT_MAX_BYTES = max(
    1024 * 1024, int(os.getenv("ATTACHMENT_MAX_BYTES", str(50 * 1024 * 1024)))
)

# Durable marketing job scheduler and its management API.
SCHEDULER_DB_PATH = os.getenv("SCHEDULER_DB_PATH", "/data/marketing-scheduler.db")
SCHEDULER_API_TOKEN = os.getenv("SCHEDULER_API_TOKEN", "")
SCHEDULER_DEFAULT_TIMEZONE = os.getenv("SCHEDULER_DEFAULT_TIMEZONE", "Asia/Shanghai")
SCHEDULER_POLL_SECONDS = max(5, int(os.getenv("SCHEDULER_POLL_SECONDS", "15")))
