<!--Copyright 2026 The HuggingFace Team. All rights reserved.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
the License. You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
-->

# Command line interface (`diffusers-cli`)

`diffusers-cli` is a command line entry point for running, inspecting, and packaging Diffusers pipelines
without writing Python. It's installed alongside the `diffusers` package.

The CLI adapts its output for whoever's reading it:

- Under an AI coding agent (Claude Code, Cursor, Aider, GitHub Copilot Agent — detected via env vars like
  `CLAUDECODE`, `CURSOR_AI`, `AIDER_AI_CONTEXT`), output switches to a compact `key=value` / TSV format with
  no progress bars.
- In a normal terminal, it prints human-friendly summaries.
- Force a specific format with `--format {auto, human, agent, json, quiet}` **before** the subcommand:
  `diffusers-cli --format json run --model ...`.

## Available commands

| Command | Purpose |
|---|---|
| [`env`](#env) | Print environment info for bug reports. |
| [`schema`](#schema) | Introspect a pipeline's `__call__` signature without downloading weights. |
| [`run`](#run) | Run a pipeline locally or submit it to Hugging Face Jobs. |
| [`custom_blocks`](#custom_blocks) | Package a local `ModularPipelineBlocks` subclass for the Hub. |
| [`fp16_safetensors`](#fp16_safetensors) | Convert a checkpoint to fp16 `.safetensors`. |
| [`skills`](#skills) | Install pre-authored skill bundles into your AI coding agent. |

> [!TIP]
> This page covers the common flags. For the full, always-current list of options for any subcommand, run `diffusers-cli <command> --help` (e.g. `diffusers-cli run --help`).

## `env`

Prints Python / PyTorch / diffusers versions, CUDA info, and installed optional deps. Use it when opening an
issue so maintainers can reproduce your setup.

```bash
diffusers-cli env
```

## `schema`

Prints the accepted `__call__` arguments for a pipeline, without downloading weights. It fetches the repo's
`model_index.json` (or `modular_model_index.json`), resolves the pipeline class, and inspects its signature.
For modular repos with custom code, pass `--trust-remote-code`.

```bash
diffusers-cli --format json schema --model black-forest-labs/FLUX.1-dev
```

## `run`

Run a pipeline end-to-end. Auto-detects standard vs modular repos, auto-loads media inputs from URLs or local
paths, saves outputs by sniffing the pipeline's return type, and can submit the same call to Hugging Face Jobs
via `--remote`.

Minimal example:

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.1-dev \
    --dtype bf16 \
    --pipeline-kwargs '{"prompt": "an astronaut riding a horse"}'
```

### Passing pipeline arguments

`--pipeline-kwargs` takes a JSON object that's forwarded to `pipeline(**kwargs)`. String values at known
media-input keys are auto-loaded:

- **Images** (`image`, `mask_image`, `control_image`, `ip_adapter_image`, `image_2`) → `PIL.Image` via
  [`load_image`].
- **Videos** (`video`, `control_video`) → `list[PIL.Image]` via [`load_video`].
- **Audio** (`initial_audio_waveforms`, `reference_audio`, `src_audio`) → `torch.Tensor` via `torchaudio.load`.

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.2-klein-9B --dtype bf16 \
    --pipeline-kwargs '{"prompt": "make the fur grey", "image": "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png", "strength": 0.6}'
```


### Loading

- `--dtype {auto, bf16, fp16, fp32, ...}` — weight dtype. See
  [Pipeline data types](../using-diffusers/loading#pipeline-data-types).
- `--device-map <value>` — component placement. Accepts a torch device string (`cuda`, `cuda:0`, `cpu`, `mps`),
  `balanced` (auto-splits components across visible GPUs), or a JSON dict for explicit per-component placement.
  Auto-detected if omitted. See [device_map](../training/distributed_inference#device_map) for details on the
  `balanced`/`auto` strategies.
- `--variant fp16` — pick a weight variant.
- `--revision <sha>` — pin a specific model revision.
- `--trust-remote-code` — allow custom code from the Hub (required for repos that ship custom pipeline classes
  or modular blocks). See [Community pipelines](../using-diffusers/custom_pipeline_overview).
- `--lora <spec>` — attach one or more LoRA adapters after loading. Accepts a single JSON object
  or a list of them. `lora_id` is required per entry; `lora_scale` defaults to `1.0`; `adapter_name`
  is optional (auto-generated as `lora_<i>` when stacking).
  - Single: `--lora '{"lora_id": "alvdansen/flux-koda", "lora_scale": 0.8}'`
  - Multiple: `--lora '[{"lora_id": "alvdansen/flux-koda", "lora_scale": 0.6, "adapter_name": "koda"}, {"lora_id": "Shakker-Labs/FLUX.1-dev-LoRA-AntiBlur", "lora_scale": 0.4}]'`

  All specs are loaded via `pipeline.load_lora_weights(...)`, then activated together via a single
  `pipeline.set_adapters(names, adapter_weights=scales)` call. See [LoRA](../tutorials/using_peft_for_inference)
  for a deeper walkthrough of adapter stacking, scale scheduling, and hotswapping.

### Optimizations

- `--cpu-offload {model, group}` — `model` calls `enable_model_cpu_offload`; `group` calls
  `enable_group_offload(offload_type="leaf_level", use_stream=True)`. Onload target comes from `--device-map`
  (which must be a plain device string for offload). See
  [Model offloading](../optimization/memory#model-offloading) and
  [Group offloading](../optimization/memory#group-offloading).
- `--attention-backend {default, flash_hub, flash_varlen_hub, flash_4_hub, sage_hub}` — Hub-hosted attention
  kernels, auto-downloaded on first use. Transformer-based pipelines only; ignored with a warning on legacy UNet
  pipelines. See [Attention backends](../optimization/attention_backends).
- `--vae-tiling` / `--vae-slicing` — lower VAE decode VRAM. See
  [VAE tiling](../optimization/memory#vae-tiling) and [VAE slicing](../optimization/memory#vae-slicing).
- `--compile [JSON]` — `torch.compile` every denoiser submodule. Prefers regional compilation via
  `compile_repeated_blocks` where available. Bare `--compile` uses `fullgraph=true`; a JSON object is forwarded
  to `torch.compile`. Currently not supported with `--context-parallel` — the CLI logs a warning and skips the
  compile step in that case. See [torch.compile](../optimization/fp16#torchcompile) and
  [Regional compilation](../optimization/fp16#regional-compilation).
- `--context-parallel` — Ulysses-style context parallelism on a DiT-based pipeline. Locally requires torchrun;
  under `--remote` the CLI wraps `torchrun --nproc-per-node=gpu` for you. See
  [Context parallelism](../training/distributed_inference#context-parallelism).

### Outputs

`run` sniffs the pipeline output type:

- `PIL.Image` / list → `<task>-<i>.png`
- Frame sequence → `<task>-0.mp4` (`--fps` controls framerate, default 8)
- Audio array → `<task>-0.wav` (`--sampling-rate` controls rate)
- Anything else → JSON dump

Default output directory is `~/.diffusers/cli/run/outputs/diffusers-run-<YYYYMMDDTHHMMSS>-<uuid>/`. Each run
gets its own subdirectory so consecutive invocations don't overwrite. Override with `--output <path>` (file or
directory) — explicit paths bypass the namespaced default and land flat.

Use `--push-to <your-bucket-id>` to upload outputs to a
[Hugging Face storage bucket](https://huggingface.co/docs/hub/en/storage-buckets). `<your-bucket-id>` is a
bare bucket id in `<namespace>/<name>` form (same shape as a Hub repo id) — `hf://` URIs are not accepted here.
The bucket is created if missing; objects land under `<run_id>/<filename>` and are addressable as
`hf://buckets/<your-bucket-id>/<run_id>/<filename>`.

| `--push-to` set? | `--output` set? | Result |
|---|---|---|
| no | no | download to default local dir |
| no | yes | download to `--output` |
| yes | no | bucket only, no local download |
| yes | yes | bucket AND `--output` |

`--format` shapes the **stdout metadata** (paths, timing, job info) — it does not change the file format of
the media itself. Written images are always PNG, videos MP4, audio WAV.

### Remote execution (`--remote`)

Submit the same call as a Hugging Face Job. See the
[HF Jobs overview](https://huggingface.co/docs/hub/en/jobs-overview) for background.

```bash
diffusers-cli run \
    --model black-forest-labs/FLUX.1-dev --dtype bf16 \
    --pipeline-kwargs '{"prompt": "an astronaut riding a horse"}' \
    --remote --flavor a100-large
```

Remote flags:

- `--flavor <name>` — HF Jobs hardware (e.g. `a10g-small`, `h200`, `rtx-pro-6000`).
- `--timeout <duration>` — max wallclock (default `10m`).
- `--dependencies <pkg>` — extra pip deps (repeatable). Useful for pinning a diffusers branch tarball or
  adding pipeline-specific extras.
- `--namespace <name>` — run under a different HF org/account.
- `--no-wait` — submit, print the job id and URL, return immediately.
- `--image <ref>` — override the container image. Must ship torch + CUDA compatible with your `--flavor`'s
  driver.

## `custom_blocks`

Package a local `ModularPipelineBlocks` subclass for upload to the Hub. Reads a Python file, AST-scans it for
subclasses of `ModularPipelineBlocks`, instantiates the chosen one, and calls `save_pretrained` in the current
working directory.

```bash
# Package the first block found in ./block.py
diffusers-cli custom_blocks

# Point at a different file / pick a specific class
diffusers-cli custom_blocks --block_module_name my_block.py --block_class_name MyDenoiseBlock
```

The block class must be instantiable with zero constructor args — hardcode defaults in `__init__` or read
config from the pipeline `state` at call time.

## `fp16_safetensors`

Convert a checkpoint on the Hub to fp16 `.safetensors` and push the result. Useful for shrinking a repo's
weight size for faster loading. See `diffusers-cli fp16_safetensors --help` for the exact args.

## `skills`

Install Agent Skills bundles from the diffusers repo (`.ai/skills/`).

```bash
# Install the diffusers-cli skill
diffusers-cli skills add diffusers-cli

# Install every skill in the registry
diffusers-cli skills add --all

# List available skills
diffusers-cli skills list

# Preview a skill's SKILL.md without installing
diffusers-cli skills preview diffusers-cli

# Refetch and reinstall every managed skill
diffusers-cli skills update

# Install to the user-level directory instead of the current project
diffusers-cli skills add diffusers-cli --global
```

`--force` overwrites an existing install. `--all` fetches every skill in the registry in one call
and downgrades individual failures to warnings so one broken skill doesn't abort the batch.

<!-- Doc link references -->
[`load_image`]: /docs/diffusers/api/utilities#diffusers.utils.load_image
[`load_video`]: /docs/diffusers/api/utilities#diffusers.utils.load_video
