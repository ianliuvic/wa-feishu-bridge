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
MAX_ATTEMPTS = max(1, int(os.getenv("INQUIRY_MAX_ATTEMPTS", "5")))
WINDOW_HOURS = max(1, int(os.getenv("INQUIRY_WINDOW_HOURS", "168")))
REOPEN_DAYS = max(1, int(os.getenv("INQUIRY_REOPEN_DAYS", "30")))
HISTORY_CHECK = os.getenv("INQUIRY_HISTORY_CHECK", "true").strip().lower() in (
    "1", "true", "yes", "on"
)

_SYSTEM = """你是红绣泳装（中国泳装/瑜伽服工厂）的询盘审核员。
任务：判断一条 WhatsApp 消息是否是【真实的泳装采购询盘】——对方是买家，想向我们采购或委托生产泳装类产品。

判 true 必须同时满足：
1. 明确涉及泳装/泳衣/比基尼/沙滩装/瑜伽服/运动内衣等我们生产的产品；
2. 明确表达买方意图：询价、要报价、要目录、打样、下单、OEM/ODM、贴牌、批发采购；
3. 有具体诉求或可执行下一步（数量、款式、目标价、交期、MOQ 至少一项，或明确要求我们报价/发目录）。

补充：这是红绣泳装公司的 WhatsApp 业务号，消息默认处于泳装业务语境。若消息是结构化报价请求（数量、private label/OEM、目标市场、交期、面料克重等），即使没有出现“泳装/泳衣”字样，也视为满足第 1 条。

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


def _parse_ts(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except Exception:
        return None


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
        self._conn_lock = threading.Lock()
        self._conn = None
        self._jid_locks: dict[str, threading.Lock] = {}
        self._jid_locks_guard = threading.Lock()

    def _jid_lock(self, jid: str) -> threading.Lock:
        """Serialize the whole screen-LLM-send path per conversation."""
        with self._jid_locks_guard:
            lock = self._jid_locks.get(jid)
            if lock is None:
                lock = threading.Lock()
                self._jid_locks[jid] = lock
            return lock

    # ---------- storage ----------

    def _db(self):
        if self._conn is not None:
            return self._conn
        with self._conn_lock:
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
                    reason TEXT,
                    status TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_message_at TEXT
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(seen)")}
            if "status" not in columns:
                conn.execute("ALTER TABLE seen ADD COLUMN status TEXT")
            if "attempts" not in columns:
                conn.execute("ALTER TABLE seen ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "last_message_at" not in columns:
                conn.execute("ALTER TABLE seen ADD COLUMN last_message_at TEXT")
            conn.execute("UPDATE seen SET status = 'known' WHERE seeded = 1 AND status IS NULL")
            conn.execute(
                "UPDATE seen SET status = CASE WHEN verdict = 1 THEN 'sent' "
                "WHEN verdict = 0 THEN 'rejected' ELSE 'pending' END WHERE status IS NULL"
            )
            conn.execute("UPDATE seen SET last_message_at = first_seen WHERE last_message_at IS NULL")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS aliases (jid TEXT PRIMARY KEY, canonical TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON aliases(canonical)"
            )
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

    def _row(self, jid: str):
        row = self._db().execute(
            "SELECT status, attempts, first_seen, last_message_at, seeded, push_name "
            "FROM seen WHERE jid = ?",
            (jid,),
        ).fetchone()
        if row is None:
            return None
        keys = ("status", "attempts", "first_seen", "last_message_at", "seeded", "push_name")
        return dict(zip(keys, row))

    def _lookup_locked(self, jids: list[str]):
        conn = self._db()
        for jid in jids:
            row = conn.execute(
                "SELECT canonical FROM aliases WHERE jid = ?", (jid,)
            ).fetchone()
            if row:
                return row[0]
        for jid in jids:
            row = conn.execute("SELECT jid FROM seen WHERE jid = ?", (jid,)).fetchone()
            if row:
                return row[0]
        return None

    def _link_locked(self, jids: list[str], canonical: str) -> None:
        conn = self._db()
        for jid in jids:
            conn.execute(
                "INSERT OR REPLACE INTO aliases (jid, canonical) VALUES (?, ?)",
                (jid, canonical),
            )

    def resolve(self, jids: list[str]):
        with self._lock:
            return self._lookup_locked(jids)

    def create_pending(self, canonical: str, jids: list[str], push_name: str) -> None:
        stamp = _now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._db()
            conn.execute(
                "INSERT OR IGNORE INTO seen (jid, first_seen, push_name, seeded, verdict, reason, "
                "status, attempts, last_message_at) VALUES (?, ?, ?, 0, NULL, '', 'pending', 0, ?)",
                (canonical, stamp, push_name or "", stamp),
            )
            self._link_locked(jids, canonical)
            conn.commit()

    def mark_known(self, canonical: str, jids: list[str], push_name: str) -> None:
        stamp = _now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._db()
            conn.execute(
                "INSERT OR IGNORE INTO seen (jid, first_seen, push_name, seeded, verdict, reason, "
                "status, attempts, last_message_at) VALUES (?, ?, ?, 1, NULL, '', 'known', 0, ?)",
                (canonical, stamp, push_name or "", stamp),
            )
            conn.execute(
                "UPDATE seen SET status = 'known', seeded = 1 WHERE jid = ?", (canonical,)
            )
            self._link_locked(jids, canonical)
            conn.commit()

    def start_screening(self, jid: str, push_name: str) -> str:
        """Decide whether this message should go to the model.

        Returns "judge", "known" (existing conversation), or "done" (already sent
        or rejected until the reopen window). A pending sender is never locked by
        a greeting: only a delivered card or the attempt/TTL cap ends screening.
        """
        now = _now()
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._db()
            row = self._row(jid)
            if row is None:
                return "done"
            if row["seeded"]:
                return "known"
            status = row["status"] or "pending"
            if status == "sent":
                return "done"
            if status == "rejected":
                last = _parse_ts(row["last_message_at"]) or now
                if (now - last).total_seconds() < REOPEN_DAYS * 86400:
                    return "done"
                conn.execute(
                    "UPDATE seen SET status = 'pending', attempts = 0, first_seen = ?, "
                    "last_message_at = ?, push_name = ?, verdict = NULL WHERE jid = ?",
                    (stamp, stamp, push_name or row["push_name"] or "", jid),
                )
                conn.commit()
                return "judge"
            # pending: start a fresh window when the old one expired
            first = _parse_ts(row["first_seen"]) or now
            if (now - first).total_seconds() > WINDOW_HOURS * 3600:
                conn.execute(
                    "UPDATE seen SET attempts = 0, first_seen = ? WHERE jid = ?",
                    (stamp, jid),
                )
            conn.execute(
                "UPDATE seen SET last_message_at = ?, push_name = ? WHERE jid = ?",
                (stamp, push_name or row["push_name"] or "", jid),
            )
            conn.commit()
            return "judge"

    def record_rejected(self, jid: str, reason: str) -> None:
        """A judged message that was not an inquiry: count it, cap the window."""
        now = _now()
        with self._lock:
            conn = self._db()
            row = self._row(jid)
            if row is None:
                return
            attempts = int(row["attempts"] or 0) + 1
            first = _parse_ts(row["first_seen"]) or now
            expired = (now - first).total_seconds() > WINDOW_HOURS * 3600
            status = "rejected" if attempts >= MAX_ATTEMPTS or expired else "pending"
            conn.execute(
                "UPDATE seen SET attempts = ?, status = ?, verdict = 0, reason = ? WHERE jid = ?",
                (attempts, status, (reason or "")[:200], jid),
            )
            conn.commit()

    def record_sent(self, jid: str, reason: str) -> None:
        with self._lock:
            conn = self._db()
            conn.execute(
                "UPDATE seen SET status = 'sent', verdict = 1, reason = ? WHERE jid = ?",
                ((reason or "")[:200], jid),
            )
            conn.commit()

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
                rows.append(
                    (jid, stamp, str(chat.get("pushName") or ""), 1, None, "known", 0, stamp)
                )
            try:
                with self._lock:
                    self._db().executemany(
                        "INSERT OR IGNORE INTO seen (jid, first_seen, push_name, seeded, verdict, "
                        "status, attempts, last_message_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                    for row in rows:
                        self._db().execute(
                            "INSERT OR REPLACE INTO aliases (jid, canonical) VALUES (?, ?)",
                            (row[0], row[0]),
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
            with self._lock:
                rows = dict(
                    self._db().execute("SELECT status, COUNT(*) FROM seen GROUP BY status").fetchall()
                )
            return {
                "tracked": sum(rows.values()),
                "known": rows.get("known", 0),
                "pending": rows.get("pending", 0),
                "inquiries": rows.get("sent", 0),
                "rejected": rows.get("rejected", 0),
            }
        except Exception:
            return {"tracked": 0, "known": 0, "pending": 0, "inquiries": 0, "rejected": 0}

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
        # Media without a caption produces "[非文字消息]" and falls through below;
        # media with a caption is a normal text inquiry (image + question).
        text = (evt.text or "").strip()
        if not text or text == "[非文字消息]":
            return False
        if not self.seed(evt.instance, evolution):
            logger.warning("inquiry: chat history not seeded yet, skipping %s", evt.remote_jid)
            return False

        jids = evt.identity_jids or [evt.remote_jid]
        canonical = evt.canonical_jid or evt.remote_jid
        with self._jid_lock(canonical):
            key = self.resolve(jids)
            if key is None:
                if HISTORY_CHECK and evolution is not None:
                    try:
                        has_history = evolution.has_history(
                            evt.instance, jids, evt.message_id, evt.timestamp
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("inquiry: history check failed for %s: %s", canonical, exc)
                        has_history = False
                    if has_history:
                        self.mark_known(canonical, jids, evt.push_name)
                        logger.info(
                            "inquiry: %s already has conversation history, marked known", canonical
                        )
                        return False
                self.create_pending(canonical, jids, evt.push_name)
                key = canonical

            action = self.start_screening(key, evt.push_name)
            if action != "judge":
                return False

            result, err = self.judge(llm, text, evt.push_name, evt.sender_phone)
            if err or result is None:
                # Leave the sender pending: the next message gets another try.
                logger.warning("inquiry: screening failed for %s: %s", key, err)
                return False

            is_inquiry = bool(result["is_inquiry"]) and result["confidence"] >= self.min_confidence
            if not is_inquiry:
                self.record_rejected(key, result["reason"])
                logger.info(
                    "inquiry: not a lead %s confidence=%.2f reason=%s (kept for next message)",
                    evt.sender_phone, result["confidence"], result["reason"],
                )
                return False

            card = self.build_card(evt, result["summary"], text, result["translation"])
            flat = self.build_card(evt, result["summary"], text, result["translation"], compact=True)
            if not self.post(card, flat):
                return False
            self.record_sent(key, result["reason"])
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
    def _plain(text):
        return (text or "").replace("<", "＜").replace(">", "＞")

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
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": InquiryNotifier._plain(text) or "（空）"}}
            ],
        }

    def _meta(self, evt: EvolutionMessage):
        private = evt.remote_jid.endswith("@lid")
        identity = "身份" if private else "号码"
        phone = evt.sender_phone + ("（WhatsApp 隐私号）" if private else "")
        return (
            f"**来源**　WhatsApp · {self._md(evt.instance, 60)}\n"
            f"**时间**　{_now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**客户**　{self._md(evt.push_name or '未命名', 60)}\n"
            f"**{identity}**　{self._md(phone, 60)}"
        )

    def build_card(self, evt: EvolutionMessage, summary, original, translation, compact=False):
        inquiry_id = "INQ-WA-" + _now().strftime("%Y%m%d-%H%M%S")
        elements = [
            {"tag": "markdown", "content": f"**{self._md(summary, 80) or '（无摘要）'}**"},
            {"tag": "markdown", "content": self._meta(evt)},
            {"tag": "hr"},
        ]
        if compact:
            elements += [
                {"tag": "markdown", "content": "**📄 原文**"},
                {"tag": "div", "text": {"tag": "plain_text", "content": self._plain(original)[:MAX_TEXT] or "（空）"}},
            ]
            if translation and translation.strip() != (original or "").strip():
                elements += [
                    {"tag": "markdown", "content": "**🌐 译文**"},
                    {"tag": "div", "text": {"tag": "plain_text", "content": self._plain(translation)[:MAX_TEXT]}},
                ]
        else:
            elements.append(self._panel("📄 原文", (original or "")[:MAX_TEXT]))
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
            "config": {"wide_screen_mode": False, "update_multi": True},
            "header": {
                "template": "green",
                "title": {"tag": "plain_text", "content": "🆕 新询盘 · WhatsApp"},
            },
            "body": {"direction": "vertical", "elements": elements},
        }

    def post(self, card, fallback=None) -> bool:
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
            if fallback is not None:
                logger.warning("inquiry: retrying with the flat card layout")
                return self.post(fallback)
            return False
        return True
