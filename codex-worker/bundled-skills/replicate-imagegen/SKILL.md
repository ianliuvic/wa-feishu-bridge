---
name: replicate-imagegen
description: Generate or edit raster images through Replicate-hosted models such as Flux. Use when Codex needs image generation via Replicate, when the user mentions Replicate, Flux on Replicate, REPLICATE_API_TOKEN, external image generation APIs, or wants to avoid the built-in OpenAI image_gen path.
---

# Replicate Imagegen

Use this skill to generate project-bound bitmap assets through Replicate instead of Codex's built-in image generation path.

## Default Path

Run the bundled CLI:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py \
  --prompt "A clean SaaS landing-page hero image, abstract workflow automation" \
  --out output/imagegen/hero.png
```

Defaults:
- Preset: `default_preset` from `config.json`, falling back to `black-forest-labs/flux-schnell`
- Output: `output/imagegen/replicate-output.png`
- Aspect ratio: preset `input.aspect_ratio`, falling back to `1:1`

Requirements:
- `REPLICATE_API_TOKEN` must be set in the environment, or `config.json` must contain `replicate_api_token`.
- Python package `replicate` must be installed in the active Python environment.

Install dependency when needed:

```bash
pip install replicate
```

Do not ask the user to paste the token in chat. Ask them to set `REPLICATE_API_TOKEN` locally and confirm.

Local file config is supported for persistent personal use. Create:

```json
{
  "replicate_api_token": "r8_your_token_here"
}
```

at:

```text
/root/.codex/skills/replicate-imagegen/config.json
```

Environment variable `REPLICATE_API_TOKEN` takes precedence over `config.json`.

`config.json` also supports fast model switching through presets:

```json
{
  "replicate_api_token": "r8_your_token_here",
  "default_preset": "flux-schnell",
  "presets": {
    "flux-schnell": {
      "model": "black-forest-labs/flux-schnell",
      "input": {
        "aspect_ratio": "1:1",
        "output_format": "png"
      }
    },
    "nano-banana-2": {
      "model": "replace-with-replicate-model-slug",
      "input": {
        "aspect_ratio": "1:1",
        "output_format": "png"
      }
    }
  }
}
```

## Model Selection

Use `--model` to change Replicate models:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py \
  --model black-forest-labs/flux-dev \
  --prompt "A cinematic product photo of a chrome desk lamp" \
  --aspect-ratio 16:9 \
  --out output/imagegen/lamp.png
```

Use `--preset` for fast switching:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py \
  --preset flux-schnell \
  --prompt "A cinematic product photo of a chrome desk lamp" \
  --aspect-ratio 16:9 \
  --out output/imagegen/lamp.png
```

List configured presets:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py --list-presets
```

`--model` overrides `--preset`. `--aspect-ratio`, `--input-json`, and repeated `--set key=value` override preset input fields.

Replicate model input schemas vary. Use generic JSON input when a model needs fields beyond prompt and aspect ratio:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py \
  --model owner/model-name \
  --input-json "{\"prompt\":\"A futuristic dashboard UI\",\"width\":1536,\"height\":1024}" \
  --out output/imagegen/dashboard.png
```

Use repeated `--set key=value` for simple overrides:

```bash
python /root/.codex/skills/replicate-imagegen/scripts/replicate_imagegen.py \
  --prompt "A minimal product mockup" \
  --set output_format=webp \
  --set num_outputs=1 \
  --out output/imagegen/mockup.webp
```

## Workflow

1. Decide whether the asset is preview-only or project-bound.
2. Build a concise prompt with subject, style, composition, dimensions or aspect ratio, and constraints.
3. Run `scripts/replicate_imagegen.py`.
4. Save project-bound assets inside the current workspace, typically under `output/imagegen/` or the app's asset directory.
5. Do not overwrite existing assets unless the user explicitly asks. Use a versioned filename or pass `--force` only when replacement is intended.
6. Report the saved path, model, and prompt used.

## Troubleshooting

- If `REPLICATE_API_TOKEN` is missing and `config.json` has no token, stop and ask the user to configure one locally.
- If `ModuleNotFoundError: replicate` appears, install `replicate` in the active Python environment.
- If Replicate rejects an input field, inspect the target model's input schema on Replicate and pass matching fields via `--input-json` or `--set`.
- If the model returns multiple outputs, the script writes numbered sibling files.