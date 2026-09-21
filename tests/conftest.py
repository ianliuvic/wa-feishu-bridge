"""Test environment, applied before any test module is imported.

Several test modules import app packages, and app.config reads its values once
at import time. Without this, whichever test file happens to import app.config
first fixes those values for the whole session - which made a later test see an
empty MARKETING_CHAT_ID and fail depending on file order.

conftest.py is imported before collection, so every module sees one
deterministic configuration.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="bridge-tests-")

os.environ.update({
    "FEISHU_APP_ID": "test-app",
    "FEISHU_APP_SECRET": "test-secret",
    "FEISHU_CHAT_ID": "oc_whatsapp",
    "MARKETING_CHAT_ID": "oc_marketing",
    "SCHEDULER_DB_PATH": os.path.join(_TMP, "scheduler.db"),
    "ATTACHMENT_DIR": os.path.join(_TMP, "attachments"),
    "CODEX_WORKER_URL": "https://codex.invalid",
    "CODEX_WORKER_TOKEN": "test-codex-token",
    "DSH_WORKER_URL": "https://dsh.invalid",
    "DSH_WORKER_TOKEN": "test-dsh-token",
})
# Left unset on purpose: the interactive chat flag must default to on.
os.environ.pop("MARKETING_CHAT_ENABLED", None)
