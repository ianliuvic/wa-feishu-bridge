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
CODEX_WORKER_URL = os.getenv("CODEX_WORKER_URL", "").rstrip("/")
CODEX_WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "")
CODEX_RUN_TIMEOUT_SECONDS = int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800"))
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
