# `diffusers-cli run` — reference

Full surface for `diffusers-cli run`. Use this file as the source of truth when constructing a `run`
invocation. The top-level [`SKILL.md`](SKILL.md) covers when to use the CLI; this file covers how.

## The schema → run flow

For any model you haven't called before, run `schema` first to learn its input contract, then `run` with
the right `--pipeline-kwargs`:

```bash
# 1. Discover what kwargs the pipeline takes (no weight download)
diffusers-cli --format json schema --model black-forest-labs/FLUX.2-klein-9B

# 2. Run it
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "Make the cats fur grey", "image": "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png"}' \
    --dtype bf16
```

`schema --format json` emits a `{task, model, pipeline_class, inputs[]}` payload where each input is
`{name, type_hint, default, required, description}`.

## Standard vs modular detection

`run` auto-detects which kind of pipeline it's calling:

1. If `model_index.json` exists on the repo → `DiffusionPipeline.from_pretrained` path.
2. Otherwise → `ModularPipeline.from_pretrained` path.

You don't need to tell it which. Modular repos must pass `--trust-remote-code` if they ship custom block code.

## `--pipeline-kwargs` semantics

A JSON object passed straight through to `pipeline(**kwargs)`. String values at known media-input keys are
auto-loaded before the pipeline is called:

- **Images** (`image`, `mask_image`, `control_image`, `ip_adapter_image`, `image_2`) → `PIL.Image.Image`
  via `diffusers.utils.load_image`. Accepts URLs or local paths.
- **Videos** (`video`, `control_video`) → `list[PIL.Image.Image]` via `diffusers.utils.load_video`.
- **Audio** (`initial_audio_waveforms`, `reference_audio`, `src_audio`) → `torch.Tensor` via `torchaudio.load`.
  For `initial_audio_waveforms`, the file's native sample rate is auto-written to
  `initial_audio_sampling_rate` if you didn't pass it explicitly.

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"image": "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png", "prompt": "make the fur grey", "strength": 0.6}'
```

Media resolution runs **before** the pipeline weights load, so a dead URL or missing file fails within
seconds instead of after a multi-minute model download.

**Shell-quoting gotcha**: the JSON must be on one line (or use `\` to line-continue). A literal newline inside the
single-quoted argument lands as a raw control char inside the string and breaks `json.loads`.

## LoRA adapters (`--lora`)

Attach one or more LoRAs after the pipeline loads via a JSON spec. `--lora` accepts a single object or a list.

Single adapter:

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "a tiny grey cat"}' \
    --lora '{"lora_id": "alvdansen/littletinies", "lora_scale": 0.8}'
```

Multiple stacked adapters:

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "a tiny grey cat"}' \
    --lora '[
      {"lora_id": "alvdansen/littletinies", "lora_scale": 0.5, "adapter_name": "style"},
      {"lora_id": "author/detail-boost", "lora_scale": 0.3}
    ]'
```

Per-entry fields: `lora_id` (required), `lora_scale` (optional, default `1.0`), `adapter_name` (optional; when
omitted while stacking, auto-generated as `lora_0`, `lora_1`, …; single-entry defaults to `"default"`).

Each entry calls `pipeline.load_lora_weights(<lora_id>, adapter_name=<name>)`. After all adapters load, one
`pipeline.set_adapters(names, adapter_weights=scales)` activates them together.

## Optimization flags

- `--dtype {auto, bf16, fp16, fp32, …}` — pipeline weight dtype. `bf16` is the right default for modern DiTs on
  A100/H100.
- `--device-map <value>` — component placement, forwarded to `from_pretrained(device_map=...)`. Accepts a plain
  torch device (`cuda`, `cuda:0`, `cpu`, `mps`), the string `balanced` (auto-splits pipeline components across
  visible GPUs), or a JSON dict `{"transformer": "cuda:0", "vae": "cuda:1"}` for explicit per-component
  placement. Auto-detects if omitted (pinned to `cuda:$LOCAL_RANK` under torchrun). `balanced` and dict values
  are incompatible with `--cpu-offload`.
- `--cpu-offload {model, group}` — `model` uses `enable_model_cpu_offload`, `group` uses
  `enable_group_offload(offload_type="leaf_level", use_stream=True)`. Use `group` to fit a 9B+ model on a single
  A100. Onload target device comes from `--device-map` (must be a plain device string in this case).
- `--attention-backend {default, flash_hub, flash_varlen_hub, flash_4_hub, sage_hub}` — hub-hosted kernels,
  auto-downloaded on first use. Failures (kernel not available, CUDA arch mismatch, network) raise a clear
  `SystemExit` listing the alternatives instead of silently reverting to the default. Only supported on
  transformer-based pipelines; UNet pipelines get a `logger.warning` and the flag is ignored.
- `--vae-tiling` / `--vae-slicing` — lower peak VAE decode VRAM.
- `--compile [JSON]` — `torch.compile` every denoiser submodule. See [Compile](#compile) below.
- `--context-parallel` — Ulysses-style context parallelism on a DiT. See [Context parallel](#context-parallel) below.

## Output handling

`run` sniffs the pipeline return type and saves accordingly:

- `PIL.Image` / list of them → `run-<i>.png`
- Frame sequence (≥2 PILs or ndarrays) → `run-0.mp4` (uses `--fps`, default 8)
- Numpy audio array → `run-0.wav` (uses `--sampling-rate`)
- Anything else → JSON dump

**Default output directory** is `~/.diffusers/cli/run/outputs/diffusers-run-<YYYYMMDDTHHMMSS>-<short-uuid>/`.
Each run gets its own subdirectory so consecutive invocations don't overwrite each other. The same run id
is used for the local dir, the container's `DIFFUSERS_CLI_RUN_ID` env var, and the bucket prefix under
`--remote`, so a run is traceable end-to-end.

Override the destination with `--output <path>` (file or directory). Explicit `--output` bypasses the
`diffusers-run-*` namespace — files land flat in the path you gave.

### `--push-to`

Upload outputs to an HF bucket. Objects land under `<bucket>/<run_id>/<filename>`. The bucket is created
if it doesn't exist. Behavior interacts with `--output` and `--remote`:

| flags | download to local? | bucket write? |
|---|---|---|
| (neither) | ✅ `~/.diffusers/cli/run/outputs/<run_id>/` | — |
| `--output /path` | ✅ `/path/` | — |
| `--push-to my/bucket` | ❌ | ✅ (bucket only) |
| `--push-to my/bucket --output /path` | ✅ | ✅ (both) |

The rule: explicit `--push-to` means "the bucket is my destination" — skip the local download unless the
user also explicitly asked for a local target via `--output`.

## Remote execution (`--remote`)

Add `--remote` to submit the same call as a Hugging Face Job. Backend overview:
<https://huggingface.co/docs/hub/en/jobs-overview>.

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "Make the cats fur grey", "image": "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png"}' \
    --remote --flavor a100-large \
    --dtype bf16 \
    --cpu-offload group
```

What happens:

1. Your HF token is picked up (from `--token` or your login).
2. `--pipeline-kwargs` are parsed locally so JSON errors fail fast (no wasted container time).
3. Any local file paths in `--pipeline-kwargs` are uploaded to the artifacts bucket and mounted into the
   container at `/mnt/inputs/<run_id>/`. Paths in the JSON are rewritten so the container reads from the mount.
4. The job runs in a pytorch container (`pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime` by default) that
   already has torch + CUDA preinstalled. Only the small Python deps (`diffusers`, `accelerate`,
   `transformers`, `safetensors`, `sentencepiece`, `ftfy`) are installed at container start.
5. Container logs stream to your terminal. If the bucket was auto-defaulted (or `--output` was set), the CLI
   downloads every artifact back into the local target after the job finishes.
6. A timing breakdown (`queued_seconds`, `run_seconds`, `total_seconds`) is printed and included in the JSON
   payload.

Flags:

- `--flavor <name>` — HF Jobs hardware (e.g. `a10g-small`, `a100-large`, `4xa100-large`).
- `--timeout <duration>` — max wallclock (e.g. `30m`, `2h`). Defaults to `10m`.
- `--dependencies <pkg>` — extra pip deps (repeatable). Appends to the defaults.
- `--namespace <name>` — run under a different account.
- `--no-wait` — submit, print the job id + URL, don't wait or download.
- `--push-to <bucket>` — see [`--push-to`](#-push-to) above. Explicit value → bucket-only; omit for
  auto-defaulted `<user>/jobs-artifacts` which downloads back.
- `--image <ref>` — override the container image. Must ship torch + CUDA; the CLI installs the small Python
  deps on top via `uv pip install --system`. Useful for pinning a specific torch or bundling extra system libs.

Notes on `--remote` argv forwarding: flags that only make sense on the calling machine (`--flavor`,
`--timeout`, `--namespace`, `--dependencies`, `--no-wait`, `--poll-interval`, `--image`, `--format`)
are stripped before the argv gets rebuilt for the container. Everything else — model, dtype,
pipeline-kwargs, optimizations, output flags — is forwarded verbatim.

## Compile

`--compile` runs `torch.compile` over every `transformer*` / `unet*` submodule on the pipeline. Prefers
regional compilation via `module.compile_repeated_blocks(**kwargs)` when the model exposes `_repeated_blocks`
— this only compiles the repeated inner blocks (the bulk of the compute) rather than the whole module, so
first-step latency is much lower. Falls back to full `torch.compile(module, **kwargs)` when no regional
metadata is declared.

```bash
# Bare — uses fullgraph=true
diffusers-cli run --model <id> --dtype bf16 --compile --pipeline-kwargs '...'

# With kwargs forwarded to torch.compile
diffusers-cli run --model <id> --dtype bf16 \
    --compile '{"mode": "max-autotune", "fullgraph": true}' \
    --pipeline-kwargs '...'
```

**When it's worth it**: multi-step generation (~50+ denoising steps). You pay a one-time compilation cost on
the first step, then every subsequent step is faster.

**Under `--remote`**: the compilation cost is paid **on every submission** — the container is ephemeral and
the compile cache doesn't survive. `--compile` under `--remote` only breaks even on very long generations.
For iterative work, compile locally and use `--remote` without `--compile` for one-shot runs.

`--compile` is **currently not supported with `--context-parallel`** — CP shards attention across ranks while
regional compile assumes a stable single-device graph. If both are set, the CLI logs a warning and skips the
compile step; CP still runs.

## Context parallel

`--context-parallel` enables Ulysses CP on a DiT-based pipeline. **Locally** the user must launch via torchrun:

```bash
torchrun --nproc-per-node=2 -m diffusers.commands.diffusers_cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "Make the cats fur grey"}' \
    --dtype bf16 \
    --context-parallel
```

**Remotely** the CLI handles the torchrun wrapping — just pass `--context-parallel` to a `--remote` invocation on
a multi-GPU flavor:

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B \
    --pipeline-kwargs '{"prompt": "Make the cats fur grey", "image": "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png"}' \
    --remote --flavor 4xa100-large \
    --dtype bf16 \
    --context-parallel
```

Inside the container, CP swaps the entrypoint to `torchrun --nproc-per-node=gpu -m
diffusers.commands.diffusers_cli`, initializes a hybrid process group (`cpu:gloo,cuda:nccl` — NCCL for the
attention all-to-all, Gloo for `ulysses_anything`'s per-rank size coordination), pins each rank to
`cuda:{LOCAL_RANK}`, and gates output saving/printing to rank 0 only.

**Memory note**: CP shards the sequence, **not the weights**. Every rank still holds the full transformer. Wins
are wall-clock attention speedup and headroom for very long sequences, not "fit a model that doesn't fit." For
weight sharding you'd want TP or FSDP — not exposed in the CLI yet.

CP is DiT-only. UNet pipelines raise a clear error directing you to a DiT pipeline (FLUX, SD3, HunyuanDiT,
AuraFlow, …).

## Output mode (`--format`)

`--format` controls the shape of **stdout metadata** (which paths were written, timing, job id, pushed
bucket URLs) — **not** the media file format. Written images are always PNG, videos MP4, audio WAV; only the
summary printed alongside them changes shape.

The CLI auto-detects when running under an AI coding agent (Claude Code, Cursor, Aider, GH Copilot Agent — via
`CLAUDECODE`, `CLAUDE_CODE`, `CURSOR_AI`, `AIDER_AI_CONTEXT`, `GH_COPILOT_AGENT`) and switches to **agent
mode** automatically — TSV tables, `key=value` results, compact JSON dicts, no progress bars.

Override explicitly with `--format {auto, human, agent, json, quiet}` placed **before** the subcommand:

```bash
diffusers-cli --format json run --model <id> --pipeline-kwargs '...'
```
