---
name: crun-video
description: Generate or edit videos through Crun using Google Gemini Omni or Grok Imagine Video 1.5 Preview. Use when the user asks Codex to create, animate, or edit video through Crun; requests Gemini Omni or Grok Imagine Video; or mentions api.crun.ai, CRUN_API_KEY, google/gemini-omni, or grok-imagine-video-1.5-preview. Do not use when another video provider is explicitly requested.
---

# Crun Video

Use Crun's asynchronous video API with:

- `google/gemini-omni` (default): text-to-video, reference-image video, and source-video editing.
- `grok-imagine-video-1.5-preview`: short video generation or animation from reference images.

## Requirements

- Read authentication only from `CRUN_API_KEY`; never print, persist, or pass it as a command argument.
- Use only publicly reachable HTTP(S) URLs for reference images, source videos, and callbacks. Do not upload local media without authorization.
- Treat task creation as a paid action. A direct request to generate or edit a video authorizes one task with the requested settings; otherwise confirm immediately before submission.
- Never automatically resubmit a failed paid task.

## Workflow

1. Convert the request into a precise prompt covering subject, action, camera, timing, lighting, style, and exclusions.
2. Select the explicitly requested model. Otherwise use Gemini Omni for general text generation or source-video editing and Grok Preview for short reference-image animation.
3. For Gemini Omni, pass `--model google/gemini-omni`; use up to 7 `--image-url` values, or up to 5 when also passing one `--video-url` with `--video-start` and `--video-end`. Use duration `4` (default), `6`, `8`, or `10`; aspect ratio `16:9` (default) or `9:16`; and resolution `720p` (default), `1080p`, or `4k`. When editing a source video, omit duration because the model determines it. Keep prompts within 1–20000 characters.
4. For Grok Preview, pass `--model grok-imagine-video-1.5-preview`; use up to 7 reference image URLs; duration 1–15 seconds (default 6); aspect ratio `auto` (default), `1:1`, `2:3`, `3:2`, `16:9`, or `9:16`; and resolution `480p` (default), `720p`, or `1080p`. Keep prompts within 1–4096 characters. Do not pass source-video options.
5. Run `scripts/crun_video.py create --prompt ... --wait --output-dir /workspace/codex-artifacts/videos/<task-name>` with the selected options.
6. Return saved files or `data.result.media_urls`. Remote media URLs may expire after about 14 days, so download durable deliverables promptly.

For production integrations, pass `--callback-url` and avoid polling. For an existing task, use `scripts/crun_video.py status TASK_ID` or `wait TASK_ID`; polling defaults to 20 seconds and stops after 20 minutes.

## API behavior

- Create: `POST https://api.crun.ai/api/v1/client/job/CreateTask`
- Status: `GET https://api.crun.ai/api/v1/client/job/TaskInfo?task_id=...`
- Authentication header: `x-api-key`
- Terminal states: `success`, `failed`
- Successful video URLs: `data.result.media_urls`

Treat HTTP failures and non-200 JSON `code` values as errors. For `401`, verify the API key; `402`, report insufficient credits; `422`, correct parameters; `429`, back off; `501`, report generation failure details.

Sources:

- https://docs.crun.ai/zh/models/google/gemini-omni
- https://docs.crun.ai/zh/models/grok-imagine/video-1-5-preview
- https://docs.crun.ai/zh/models/common/get-task-info
