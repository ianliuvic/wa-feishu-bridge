"""Harness-managed credential fallback for skill scripts.

Why this exists
---------------
The DeepSeek Harness removes every ambient variable whose name matches
``KEY|PASSWORD|SECRET|TOKEN`` from the environment it hands to shell commands
(``@deepseek-ai/dsh-subprocess``, ``SENSITIVE_ENV_PATTERN``). Codex did the
opposite - ``shell_environment_policy.inherit=all`` - so skills written for
Codex could just read ``os.environ``.

Rather than defeat that scrub globally, the dsh-worker entrypoint materializes
the same credentials into a root-owned, mode-0600 file and this module copies
the values a script needs into its own process environment. The scope is the
one skill process, not every command the model runs.

On a Codex worker the file simply does not exist and ``load()`` is a no-op, so
the same skill source keeps working on both executors.
"""

from __future__ import annotations

import os
from pathlib import Path

CREDENTIAL_FILE = Path(
    os.environ.get("DSH_SKILL_CREDENTIALS", "/root/.dsh-skill-credentials.env")
)


def _read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value
    return values


def load(*keys: str) -> int:
    """Copy credentials into this process's environment.

    Only names that are absent or empty are set, so a real environment value -
    on Codex, or a deliberate override - always wins. Pass specific keys to
    load only what this script needs; with no arguments every entry in the file
    is considered.

    @param keys - credential names to load; empty means all of them.
    @returns how many variables were set.
    """
    if not CREDENTIAL_FILE.is_file():
        return 0
    values = _read(CREDENTIAL_FILE)
    wanted = keys or tuple(values)
    applied = 0
    for key in wanted:
        if os.environ.get(key):
            continue
        value = values.get(key)
        if value:
            os.environ[key] = value
            applied += 1
    return applied
