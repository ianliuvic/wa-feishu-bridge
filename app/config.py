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
