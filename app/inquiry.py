"""First-message screening for WhatsApp and delivery to the dedicated Feishu group.

Only the first text message from a previously unseen number is screened, and only
a confident, concrete swimwear buyer inquiry is forwarded. The card is read-only:
the group gets a Feishu custom-bot webhook, so there are no buttons or callbacks.

Existing chats are seeded from Evolution on the first run, so an old customer who
was never saved to the phone book does not look like a new lead.
"""

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import httpx

from .evolution import EvolutionMessage

logger = logging.getLogger("bridge.inquiry")

BEIJING = timezone(timedelta(hours=8))
MAX_TEXT = 4000

_SYSTEM = """你是红绣泳装（中国泳装/瑜伽服工厂）的询盘审核员。
任务：判断一条 WhatsApp 消息是否是【真实的泳装采购询盘】——对方是买家，想向我们采购或委托生产泳装类产品。

判 true 必须同时满足：
1. 明确涉及泳装/泳衣/比基尼/沙滩装/瑜伽服/运动内衣等我们生产的产品；
2. 明确表达买方意图：询价、要报价、要目录、打样、下单、OEM/ODM、贴牌、批发采购；
3. 有具体诉求或可执行下一步（数量、款式、目标价、交期、MOQ 至少一项，或明确要求我们报价/发目录）。

以下一律判 false：
- 钓鱼/诈骗：要求投资、转账、索要银行账户/证件/验证码、承诺大单但先要费用、要求点击外部链接；
- 推销：货代、面料辅料、包装、代运营、建站、SEO、广告、网红、招聘；
- 只寒暄（Hi/Hello/在吗）、只发表情、正文为空或语义不明；
- 群发广告、通知、自动回复；
- 只泛泛说“想合作/代理/分销”，没有任何泳装细节或采购诉求。

不确定时一律判 false（宁缺勿滥）。

严格输出 JSON，不要输出其他文字：
{"is_inquiry": true|false, "confidence": 0到1的小数, "reason": "≤30字中文理由", "summary": "≤40字中文摘要", "translation": "消息的完整中文翻译"}
若 is_inquiry=false：summary 与 translation 均返回空字符串；原文已是中文时 translation 也返回空字符串。"""


def _now():
    return datetime.now(BEIJING)


class InquiryNotifier:
    def __init__(
        self,
        webhook_url: str,
        db_path: str,
        *,
        min_confidence: float = 0.65,
    ):
        self.webhook_url = (webhook_url or "").strip()
        self.db_path = db_path
        self.min_confidence = min_confidence
        self._lock = threading.Lock()
        self._seed_lock = threading.Lock()
        self._conn = None

    # ---------- storage ----------

    def _db(self):
        if self._conn is not None:
            return self._conn
        directory = os.path.dirname(self.db_path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            pass
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                jid TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                push_name TEXT,
                seeded INTEGER NOT NULL DEFAULT 0,
                verdict INTEGER,
                reason TEXT
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        self._conn = conn
        return conn

    def _seeded(self, instance: str) -> bool:
        row = self._db().execute(
            "SELECT 1 FROM meta WHERE key = ?", (f"seeded:{instance}",)
        ).fetchone()
        return row is not None

    def _mark_seeded(self, instance: str) -> None:
        with self._lock:
            self._db().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"seeded:{instance}", _now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._db().commit()

    def known(self, jid: str) -> bool:
        try:
            row = self._db().execute("SELECT 1 FROM seen WHERE jid = ?", (jid,)).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("inquiry db read failed: %s", exc)
            return False

    def claim(self, jid: str, push_name: str) -> bool:
        """Reserve a sender before screening. False means it was already seen."""
        try:
            with self._lock:
                cur = self._db().execute(
                    "INSERT OR IGNORE INTO seen (jid, first_seen, push_name, seeded, verdict) "
                    "VALUES (?, ?, ?, 0, NULL)",
                    (jid, _now().strftime("%Y-%m-%d %H:%M:%S"), push_name or ""),
                )
                self._db().commit()
                return cur.rowcount == 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("inquiry db claim failed: %s", exc)
            return False

    def settle(self, jid: str, verdict: bool, reason: str) -> None:
        try:
            with self._lock:
                self._db().execute(
                    "UPDATE seen SET verdict = ?, reason = ? WHERE jid = ?",
                    (1 if verdict else 0, (reason or "")[:200], jid),
                )
                self._db().commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("inquiry db settle failed: %s", exc)

    def release(self, jid: str) -> None:
        """Drop a reservation when screening failed, so a later message can retry."""
        try:
            with self._lock:
                self._db().execute("DELETE FROM seen WHERE jid = ?", (jid,))
                self._db().commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("inquiry db release failed: %s", exc)

    def seed(self, instance: str, evolution) -> bool:
        """Mark every existing Evolution chat as known, once per instance.

        Call this at startup, before webhook traffic is screened: Evolution
        creates a chat as soon as its first message arrives, so seeding after the
        fact would make a genuinely new sender look like an old contact.
        """
        if evolution is None:
            return False
        with self._seed_lock:
            if self._seeded(instance):
                return True
            try:
                chats = evolution.find_chats(instance)
            except Exception as exc:  # noqa: BLE001
                logger.warning("inquiry seed failed (instance=%s): %s", instance, exc)
                return False
            rows = []
            stamp = _now().strftime("%Y-%m-%d %H:%M:%S")
            for chat in chats or []:
                jid = str(chat.get("remoteJid") or "")
                if not jid:
                    continue
                if jid.endswith("@g.us") or jid.endswith("@broadcast"):
                    continue
                rows.append((jid, stamp, str(chat.get("pushName") or ""), 1, None))
            try:
                self._db().executemany(
                    "INSERT OR IGNORE INTO seen (jid, first_seen, push_name, seeded, verdict) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                self._db().commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("inquiry seed write failed: %s", exc)
                return False
            self._mark_seeded(instance)
            logger.info("inquiry seed: %s known chats for %s", len(rows), instance)
            return True

    def counts(self) -> dict:
        try:
            total = self._db().execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            inquiries = self._db().execute(
                "SELECT COUNT(*) FROM seen WHERE verdict = 1"
            ).fetchone()[0]
            return {"known": total, "inquiries": inquiries}
        except Exception:
            return {"known": 0, "inquiries": 0}

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---------- screening ----------

    def screen(self, evt: EvolutionMessage, llm, evolution) -> bool:
        """Screen the event's text; return True when a card was delivered."""
        if not self.webhook_url:
            return False
        if evt.is_group:
            return False
        # Voice / image / file / video as the first message: skipped by design.
        if evt.media_type != "none":
            return False
        text = (evt.text or "").strip()
        if not text or text == "[非文字消息]":
            return False
        if not self.seed(evt.instance, evolution):
            logger.warning("inquiry: chat history not seeded yet, skipping %s", evt.remote_jid)
            return False

        jid = evt.remote_jid
        if not self.claim(jid, evt.push_name):
            return False

        result, err = self.judge(llm, text, evt.push_name, evt.sender_phone)
        if err or result is None:
            logger.warning("inquiry: screening failed for %s: %s", jid, err)
            self.release(jid)
            return False

        is_inquiry = bool(result["is_inquiry"]) and result["confidence"] >= self.min_confidence
        self.settle(jid, is_inquiry, result["reason"])
        if not is_inquiry:
            logger.info(
                "inquiry: not a lead %s confidence=%.2f reason=%s",
                evt.sender_phone, result["confidence"], result["reason"],
            )
            return False

        card = self.build_card(evt, result["summary"], text, result["translation"])
        if not self.post(card):
            return False
        logger.info(
            "inquiry: forwarded %s confidence=%.2f", evt.sender_phone, result["confidence"]
        )
        return True

    def judge(self, llm, text, push_name, phone):
        if llm is None:
            return None, "no deepseek key"
        user = f"客户名称: {push_name or '(未知)'}\n号码: {phone}\n消息:\n{text[:MAX_TEXT]}"
        try:
            content = llm.chat(user, system=_SYSTEM, temperature=0, json_mode=True)
        except Exception as exc:  # noqa: BLE001
            return None, f"llm error: {exc}"
        try:
            obj = json.loads(content)
        except Exception:
            match = re.search(r"\{.*\}", content or "", re.S)
            if not match:
                return None, f"json parse error: {(content or '')[:200]}"
            try:
                obj = json.loads(match.group(0))
            except Exception:
                return None, f"json parse error: {(content or '')[:200]}"
        try:
            confidence = float(obj.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "is_inquiry": bool(obj.get("is_inquiry")),
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(obj.get("reason") or "").strip()[:120],
            "summary": str(obj.get("summary") or "").strip()[:200],
            "translation": str(obj.get("translation") or "").strip()[:MAX_TEXT],
        }, None

    # ---------- card ----------

    @staticmethod
    def _md(value, limit=200):
        value = " ".join((value or "").split())
        return value.replace("<", "＜").replace(">", "＞")[:limit]

    @staticmethod
    def _panel(title, text):
        return {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
                "title": {"tag": "markdown", "content": title},
                "vertical_align": "center",
                "icon": {
                    "tag": "standard_icon",
                    "token": "down-small-ccm_outlined",
                    "size": "16px 16px",
                },
                "icon_position": "right",
                "icon_expanded_angle": -180,
            },
            "border": {"color": "grey", "corner_radius": "5px"},
            "padding": "8px 8px 8px 8px",
            "elements": [{"tag": "markdown", "content": (text or "（空）").replace("<", "＜").replace(">", "＞")}],
        }

    def build_card(self, evt: EvolutionMessage, summary, original, translation):
        private = evt.remote_jid.endswith("@lid")
        identity = "身份" if private else "号码"
        phone = evt.sender_phone + ("（WhatsApp 隐私号）" if private else "")
        inquiry_id = "INQ-WA-" + _now().strftime("%Y%m%d-%H%M%S")
        elements = [
            {"tag": "markdown", "content": f"**{self._md(summary, 80) or '（无摘要）'}**"},
            {
                "tag": "markdown",
                "content": (
                    f"**来源**　WhatsApp · {self._md(evt.instance, 60)}\n"
                    f"**时间**　{_now().strftime('%Y-%m-%d %H:%M')}\n"
                    f"**客户**　{self._md(evt.push_name or '未命名', 60)}\n"
                    f"**{identity}**　{self._md(phone, 60)}"
                ),
            },
            {"tag": "hr"},
            self._panel("📄 原文", (original or "")[:MAX_TEXT]),
        ]
        if translation and translation.strip() != (original or "").strip():
            elements.append(self._panel("🌐 译文", translation[:MAX_TEXT]))
        elements += [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"<font color='grey'>{inquiry_id}</font>",
            },
        ]
        return {
            "schema": "2.0",
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": {
                "template": "green",
                "title": {"tag": "plain_text", "content": "🆕 新询盘 · WhatsApp"},
            },
            "body": {"direction": "vertical", "elements": elements},
        }

    def post(self, card) -> bool:
        try:
            resp = httpx.post(
                self.webhook_url,
                json={"msg_type": "interactive", "card": card},
                timeout=30,
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("inquiry webhook failed: %s", exc)
            return False
        if data.get("code") not in (0, None):
            logger.warning("inquiry webhook rejected: %s %s", data.get("code"), data.get("msg"))
            return False
        return True
