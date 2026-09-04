---
name: marketing-scheduler
description: Create, list, pause, resume, run, and delete durable scheduled marketing jobs. Use when a user asks Codex to do marketing work later, at a specific time, or on a recurring schedule.
---

# Marketing Scheduler

Use this skill for durable one-off or recurring marketing work. The scheduler is external to the
Codex conversation, so jobs continue after the current execution exits.

Before creating or materially changing a job, make the schedule, timezone, task prompt, and target
chat explicit. If any of them is ambiguous, ask the user. Do not create duplicate jobs when the user
is only asking what is possible.

Run the bundled CLI:

```bash
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py list
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py create \
  --name "每日广告素材" \
  --cron "0 10 * * *" \
  --timezone "Asia/Shanghai" \
  --prompt "每天围绕当前营销主题创建一条广告文案和配图，发回飞书审核"
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py update \
  --id TASK_ID --name "每日广告素材" --cron "0 10 * * *" \
  --timezone "Asia/Shanghai" --prompt "更新后的任务" --chat-id CHAT_ID
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py pause --id TASK_ID
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py resume --id TASK_ID
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py run --id TASK_ID
python3 /root/.codex/skills/marketing-scheduler/scripts/scheduler_api.py delete --id TASK_ID
```

`MARKETING_SCHEDULER_URL` and `MARKETING_SCHEDULER_TOKEN` must be available in the environment.
Never print the token. Report the returned task id, normalized schedule, timezone, and next run.
