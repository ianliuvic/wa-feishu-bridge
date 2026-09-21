---
name: crun-imagen
description: Generate or edit raster images through Crun using OpenAI GPT-Image-2, Grok Imagine Image 2.0, or Google Nano Banana 2. Use when the user asks to create or edit images through Crun, requests GPT Image 2, Grok Imagine Image 2.0, or Nano Banana 2, or mentions api.crun.ai, CRUN_API_KEY, openai/gpt-image-2, grok-imagine-image-2.0, google/nano-banana-2, or google/nano-banana-2-v2. Do not use when another image provider is explicitly requested.
---

# Crun Imagen

Use Crun's asynchronous image API with either:

- `openai/gpt-image-2` (default): text-to-image and reference-image-guided editing.
- `grok-imagine-image-2.0`: text-to-image generation with up to 12 outputs per task.
- `google/nano-banana-2`: fast text-to-image, editing, multi-image composition, extreme aspect ratios, and optional Google Search grounding.
- `google/nano-banana-2-v2`: lower-cost Nano Banana 2 variant (about 40% cheaper, potentially slower) without Google Search or output-format controls.

## Requirements

- Read the API key from `CRUN_API_KEY`; never print, persist, or place it in command arguments.
- Reference images must be reachable HTTP(S) URLs. If the user supplies only local files, explain that they must first be hosted at accessible URLs; do not upload them without authorization.
- Creating a task consumes Crun credits. A direct request to generate or edit an image authorizes one task with the requested settings. Otherwise, obtain confirmation immediately before submission.

## Workflow

1. Translate the user's visual request into a precise prompt, preserving requested text verbatim.
2. Select the model explicitly when the user names one. Otherwise use `openai/gpt-image-2` for backward compatibility; prefer Grok when one task needs multiple text-to-image outputs, Nano Banana 2 for Google-grounded or multi-image composition, and Nano Banana 2 v2 when lower cost matters more than latency.
3. For GPT Image 2, use aspect ratio `1:1`, `2:3`, `3:2`, `9:16`, `16:9`, `4:3`, `3:4`, `21:9`, or `auto` (default); resolution `1K` (default), `2K`, or `4K`; and up to 16 reference image URLs. Enforce: `4K` cannot use `1:1`, `auto` supports only `1K`, and prompt length is 1–5000.
4. For Grok Imagine Image 2.0, pass `--model grok-imagine-image-2.0`; use aspect ratio `1:1` (default), `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, or `16:9`; use `--n 1..12` (default 1); and keep prompt length within 1–20000. Do not pass `--image-url` or `--resolution`.
5. For Nano Banana 2, pass `--model google/nano-banana-2` or `google/nano-banana-2-v2`; use up to 14 reference image URLs, resolution `1K` (default), `2K`, or `4K`, and aspect ratio `1:1` (default), `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `1:4`, `4:1`, `1:8`, `8:1`, `21:9`, or `auto`. Keep prompt length within 1–20000. The primary model also supports `--google-search` and `--output-format png|jpg` (default `png`); never pass either option to v2.
6. Run `scripts/crun_imagen.py create --prompt ... --wait` with the appropriate options. Use `--output-dir` when the user wants local files.
7. On success, report the saved files or returned `media_urls`. Media URLs may expire after about 14 days, so download them when durable output is requested.

For production integrations, pass `--callback-url` and avoid polling. For an existing task, use `scripts/crun_imagen.py status TASK_ID` or `wait TASK_ID`; polling defaults to 15 seconds and stops after 20 minutes.

## API behavior

- Create: `POST https://api.crun.ai/api/v1/client/job/CreateTask`
- Status: `GET https://api.crun.ai/api/v1/client/job/TaskInfo?task_id=...`
- Authentication header: `x-api-key`
- Terminal task states are `success` and `failed`; successful media URLs are under `data.result.media_urls`.

Treat HTTP failures and non-200 JSON `code` values as errors. For `401`, verify the API key; `402`, report insufficient credits; `422`, correct parameters; `429`, back off; `501`, report generation failure details. Do not automatically resubmit a failed paid task.

Sources:

- https://docs.crun.ai/zh/models/openai/gpt-image-2
- https://docs.crun.ai/zh/models/grok-imagine/image-2-0
- https://docs.crun.ai/zh/models/google/nano-banana-2
