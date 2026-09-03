---
name: reddit-ops
description: Operate the persistent Coolify Reddit browser for manual login, account-state checks, Reddit research, screenshots, and explicitly approved joins of non-private communities. Do not publish, comment, vote, or send DMs.
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

Inspect membership without changing it:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py community ecommerce
```

Join a public or restricted community only after the user explicitly approves that exact subreddit. Private communities are blocked. The confirmation flag is mandatory and the service verifies membership afterward:

```bash
python3 /root/.codex/skills/reddit-ops/scripts/reddit_ops.py join ecommerce --confirm
```

## Safety and account handling

- Google credentials, MFA codes, CAPTCHA responses, and recovery prompts must be entered by the user in noVNC. Never request that they be placed in environment variables or chat.
- Never attempt CAPTCHA bypass, fingerprint spoofing, proxy rotation, mass account creation, or other anti-abuse evasion.
- Treat `authenticated: false` as requiring human login; do not loop login attempts.
- Joining is allowed only through the dedicated `join` command after the user approves the exact subreddit. Do not infer approval from a general Reddit research request.
- Do not post, comment, vote, leave communities, or send messages using browser improvisation. Those actions require dedicated endpoints and a separate human approval workflow.
- Respect subreddit rules, Reddit platform rules, and rate limits. Prefer useful participation and research over repetitive promotion.
- Never print `REDDIT_OPS_API_KEY`. The script reads `REDDIT_OPS_URL` and `REDDIT_OPS_API_KEY` from the environment.
