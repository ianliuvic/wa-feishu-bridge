"""Off-hours auto-reply: when a WhatsApp message arrives between 00:00-08:00
(Asia/Shanghai), reply ONCE per customer per window with an LLM-generated
short message in the customer's language (DeepSeek detects the language).
"""

import logging
from datetime import datetime, timedelta, timezone

from .evolution import EvolutionClient, EvolutionMessage
from .llm import DeepSeekClient

logger = logging.getLogger("bridge.autoreply")

SHANGHAI_TZ = timezone(timedelta(hours=8))

SYSTEM_PROMPT = (
    "你是一个服装公司（Hongxiu Clothing，红绣服装）的WhatsApp自动回复助手。"
    "现在是公司休息时间（北京时间00:00-08:00），你正在回复一位刚发来消息的客户。要求：\n"
    "1. 用与客户消息相同的语言回复（客户用英文就回英文，用西语就回西语，用中文就回中文）。\n"
    "2. 内容简短（1-2句话），表达：我们正在休息/睡觉、消息已收到、早上8点后第一时间回复、"
    "请先把需求留言在对话里。语气礼貌友好，可带一个😴表情。\n"
    "3. 不要添加营销话术或多余内容，只回复上面的意思。\n"
    "4. 如果客户消息没有文字内容（如只发了图片），无法判断语言时，默认用简体中文回复，保持同样含义。"
)


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
        reply = reply.strip()
        self.evolution.send_text(evt.instance, evt.sender_phone, reply)
        self.mark_replied(evt.remote_jid)
        logger.info("auto-replied to %s (%s): %s", evt.remote_jid, evt.sender_phone, reply[:80])
        return reply
