"""Off-hours auto-reply: when a WhatsApp message arrives between 00:00-08:00
(Asia/Shanghai), reply ONCE per customer per window with an LLM-generated
short message in the customer's language (DeepSeek detects the language).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from .evolution import EvolutionClient, EvolutionMessage
from .llm import DeepSeekClient

logger = logging.getLogger("bridge.autoreply")

SHANGHAI_TZ = timezone(timedelta(hours=8))

SYSTEM_PROMPT = (
    "You are the WhatsApp after-hours assistant for Hongxiu Clothing. "
    "It is currently outside business hours (00:00-08:00 China Standard Time).\n"
    "MANDATORY LANGUAGE RULE: Reply ONLY in English, regardless of the language used by the customer. "
    "Never output Chinese, Spanish, or any other language.\n"
    "Keep the reply to 1-2 short, polite sentences. Say that the message has been received, "
    "the team is currently resting, and the customer will receive a reply after 8:00 AM China time. "
    "Invite the customer to leave their requirements in the conversation. Do not add marketing content."
)

ENGLISH_FALLBACK = (
    "Thanks for your message! Our team is currently resting and will reply after 8:00 AM China time. "
    "Please feel free to leave your requirements here in the meantime."
)


def _ensure_english(reply: str) -> str:
    """Guarantee that an accidental Chinese model response is never sent."""
    reply = (reply or "").strip()
    if not reply or re.search(r"[\u3400-\u9fff]", reply):
        return ENGLISH_FALLBACK
    return reply


class AutoReplyManager:
    def __init__(
        self,
        llm: DeepSeekClient,
        evolution: EvolutionClient,
        start_hour: int = 0,
        end_hour: int = 8,
    ):
        self.llm = llm
        self.evolution = evolution
        self.start_hour = start_hour
        self.end_hour = end_hour
        self._replied: dict[str, set[str]] = {}  # "YYYY-MM-DD" -> {remote_jid}

    def is_off_hours(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(SHANGHAI_TZ)
        return self.start_hour <= now.hour < self.end_hour

    def _today(self, now: datetime | None = None) -> str:
        return (now or datetime.now(SHANGHAI_TZ)).strftime("%Y-%m-%d")

    def _prune(self):
        today = self._today()
        for day in [d for d in self._replied if d != today]:
            del self._replied[day]

    def already_replied(self, jid: str) -> bool:
        self._prune()
        return jid in self._replied.get(self._today(), set())

    def mark_replied(self, jid: str):
        self._prune()
        self._replied.setdefault(self._today(), set()).add(jid)

    def maybe_reply(self, evt: EvolutionMessage, now: datetime | None = None) -> str | None:
        """Generate + send an auto-reply if applicable. Returns the reply text, or None."""
        if not self.is_off_hours(now):
            logger.info("not off-hours, skip auto-reply")
            return None
        if evt.is_group:
            logger.info("group/broadcast message, skip auto-reply")
            return None
        if self.already_replied(evt.remote_jid):
            logger.info("already auto-replied to %s in this window", evt.remote_jid)
            return None

        # deepseek-v4-flash is a reasoning model: it first emits reasoning_content,
        # so max_tokens must leave headroom for the chain-of-thought + the reply.
        reply = self.llm.chat(user_text=evt.text, system=SYSTEM_PROMPT, temperature=0.4, max_tokens=1024)
        reply = _ensure_english(reply)
        self.evolution.send_text(evt.instance, evt.sender_phone, reply)
        self.mark_replied(evt.remote_jid)
        logger.info("auto-replied to %s (%s): %s", evt.remote_jid, evt.sender_phone, reply[:80])
        return reply
