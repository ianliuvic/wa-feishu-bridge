# dsh-worker

HTTP worker that runs DeepSeek Harness (`dsh`) tasks. It is the DSH counterpart of
[`../codex-worker`](../codex-worker) and keeps the same HTTP contract, so a caller
can be pointed at either worker.

## Why it exists

The Coolify-hosted scheduler currently executes its scheduled jobs through
`codex-worker`. This worker installs the harness in a container on the same host so
DSH can be evaluated as a drop-in executor before any scheduled job is migrated.

## HTTP contract

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness, `dsh` version, profile, capacity, whether credentials are present |
| `POST /v1/runs` | `DSH_WORKER_TOKEN` bearer | Run one task, return the final answer and any new artifacts |
| `GET /v1/artifacts/{path}` | `DSH_WORKER_TOKEN` bearer | Download a file a run produced |

`POST /v1/runs` body:

```json
{
  "prompt": "…",
  "workspace": "/workspace",
  "artifact_dir": "dsh-artifacts/2026-09-21-daily-report",
  "run_id": "optional-caller-supplied-id"
}
```

`artifact_dir` is optional. When supplied it must be a directory inside
`/workspace/dsh-artifacts`; the run then owns it exclusively and only files it
creates or changes are reported back as artifacts.

## Differences from codex-worker

- **One-shot only.** DSH's `headless` profile answers exactly one task per process
  and accepts no resume argument, so `session_id` is always `null` and `resumed` is
  always `false`. Multi-step jobs must carry their own state in the workspace
  (for example a `workflow-state.json`) instead of relying on session resume.
- **Output split.** DSH streams provider reasoning to stderr and prints only the
  final assistant message to stdout. stdout becomes `response`; stderr is kept as
  run-log evidence at `/workspace/dsh-run-logs/<run_id>/stderr.log`.
- **Exit codes.** 0 means the task completed; 1 means it aborted or errored, and the
  server maps that to HTTP 502 with the extracted `dsh: <code>: <message>`.
- **No LinkedIn/OAuth surface.** Those endpoints in `codex-worker` are Codex-worker
  specific and are not reproduced here.

## Skills: reusing the Codex skill set

DSH and Codex use the **same skill format** — a directory bundle `<name>/SKILL.md`
whose YAML frontmatter carries `name` and `description`, with `scripts/`,
`references/`, and `assets/` alongside it. DSH additionally accepts optional
`whenToUse`, `metadata`, `disable-model-invocation`, and `user-invocable`.
Everything codex-worker ships already satisfies this, so **no skill is rewritten
or converted**.

The image installs the Codex skill set at `/root/.codex/skills` — the same
absolute path codex-worker uses — and the entrypoint points the harness's
filesystem skill provider at it:

```yaml
# $DSH_HOME/cordis.patch.yml (written on first start, then preserved)
- id: skill-filesystem
  config:
    customSkillDirs:
      - /root/.codex/skills
```

Keeping the path identical is the load-bearing part. Four skill bodies
(`hongxiu-weekly-product-email`, `marketing-scheduler`, `reddit-ops`,
`shopify-analytics`) hardcode `/root/.codex/skills/<name>/scripts/...`, so both
executors keep working against one skill tree and the scheduled prompts need no
edits. Override the root with `DSH_SKILL_ROOT` if you ever need to.

DSH's provider scans these roots in rank order:

| Rank | Source | Path |
|---|---|---|
| 100 | project | `<projectRoot>/.dsh/skills` |
| 200 | project | `<projectRoot>/.agents/skills` |
| 300 | custom | `customSkillDirs` |
| 400 | user | `$DSH_HOME/skills` |
| 500 | shared | `~/.agents/skills` |

Two consequences worth knowing:

- Only `<name>/SKILL.md` directly under a root is discovered; nested `**/SKILL.md`
  is deliberately ignored. The Codex layout already matches.
- A duplicate `name` across roots is resolved by rank, so a leftover directory like
  `1688-collector-ops.backup-<date>/` still declares `name: 1688-collector-ops` and
  competes with the real one. Rank decides, not directory name.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DSH_WORKER_TOKEN` | — | Bearer token for `/v1/runs`; required in practice |
| `DEEPSEEK_API_KEY` | — | Credential the `deepseek-official` provider resolves per request |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Provider endpoint override |
| `DSH_HOME` | `/root/.dsh` | Harness home: profiles, sessions, skills, credentials |
| `DSH_PROFILE` | `headless` | Profile to boot |
| `DSH_PERMISSION_MODE` | `danger-full-access` | Sets the sandbox **and** pins approval to `never` |
| `DSH_WORKSPACE` | `/workspace` | Root the worker will run inside |
| `DSH_MAX_CONCURRENT_RUNS` | `2` | In-process run semaphore |
| `DSH_RUN_TIMEOUT_SECONDS` | `1800` | Per-run wall clock before the process is killed |
| `DSH_MAX_PROMPT_BYTES` | `120000` | Rejected above this, because the task travels as one argv entry |

`DSH_PERMISSION_MODE=danger-full-access` is load-bearing for unattended operation:
the `dsh-base` approval row derives its policy from this variable and only yields
`never` for that value. Any other value would make a run block waiting for an
approval that no one can give.

## Build and run

```sh
docker build -t dsh-worker .
docker run --rm -p 8080:80 \
  -e DSH_WORKER_TOKEN=… \
  -e DEEPSEEK_API_KEY=… \
  -v dsh-home:/root/.dsh \
  -v dsh-workspace:/workspace \
  dsh-worker
```

## Smoke test

```sh
curl -fsS http://127.0.0.1:8080/health
curl -fsS -X POST http://127.0.0.1:8080/v1/runs \
  -H "Authorization: Bearer $DSH_WORKER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Reply with exactly DSH_WORKER_SMOKE_OK"}'
```
