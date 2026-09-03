---
name: reddit-ops
description: Operate the persistent Coolify Reddit browser for manual login, account-state checks, Reddit navigation, DOM/text snapshots, and screenshots. Use for Reddit research and browser diagnostics; do not publish, comment, vote, or send DMs without a separately implemented approval workflow.
---

# Reddit Ops

Use the dedicated persistent Reddit browser instead of the worker's general Playwright browser. It keeps one browser profile across deployments and serializes commands to avoid profile locks and conflicting page actions.

## Commands

Check service and login state:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py status
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py snapshot
```

Open the Reddit login page before asking the user to authenticate through noVNC:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py open-login
```

Navigate only to a Reddit URL and read the resulting DOM snapshot:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py navigate 'https://www.reddit.com/r/swimwear/'
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py snapshot --text-limit 30000 --link-limit 200
```

Capture the visible page or full page. The downloaded PNG is written to `/workspace/codex-artifacts` by default, where the bridge can send it to Feishu:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py screenshot
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py screenshot --full-page
```

## Safety and account handling

- Google credentials, MFA codes, CAPTCHA responses, and recovery prompts must be entered by the user in noVNC. Never request that they be placed in environment variables or chat.
- Never attempt CAPTCHA bypass, fingerprint spoofing, proxy rotation, mass account creation, or other anti-abuse evasion.
- Treat `authenticated: false` as requiring human login; do not loop login attempts.
- Initial release is read-only. Do not post, comment, vote, join communities, or send messages using browser improvisation. Those actions require explicit endpoints and a human approval workflow.
- Respect subreddit rules, Reddit platform rules, and rate limits. Prefer useful participation and research over repetitive promotion.
- Never print `REDDIT_OPS_API_KEY`. The script reads `REDDIT_OPS_URL` and `REDDIT_OPS_API_KEY` from the environment.
