"""In-memory mapping of Feishu message_id -> WhatsApp customer, used so that
members replying to a forwarded card in the Feishu group reach the right
WhatsApp contact. Entries expire after TTL and the map is capped.
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("bridge.replymap")

DEFAULT_TTL_SECONDS = 48 * 3600
DEFAULT_MAX_ENTRIES = 5000


@dataclass
class ReplyTarget:
    remote_jid: str
    phone: str
    instance: str
    ts: float


class ReplyMap:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._map: dict[str, ReplyTarget] = {}

    def register(self, message_id: str, remote_jid: str, phone: str, instance: str):
        if not message_id:
            return
        self._prune()
        self._map[message_id] = ReplyTarget(remote_jid, phone, instance, time.time())
        logger.info("registered reply target: msg=%s jid=%s", message_id[:24], remote_jid)

    def get(self, message_id: str) -> ReplyTarget | None:
        self._prune()
        target = self._map.get(message_id)
        if target is None:
            logger.info("no reply target for msg=%s", message_id[:24])
        return target

    def size(self) -> int:
        return len(self._map)

    def _prune(self):
        now = time.time()
        expired = [mid for mid, t in self._map.items() if now - t.ts > self._ttl]
        for mid in expired:
            del self._map[mid]
        if len(self._map) > self._max:
            # drop oldest entries
            for mid in sorted(self._map, key=lambda m: self._map[m].ts)[: len(self._map) - self._max]:
                del self._map[mid]
