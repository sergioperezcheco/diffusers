# Copyright 2026 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Minimal unit tests for the non-``skills`` CLI commands.

Covers the contracts that catch real bugs: JSON kwargs parsing, media auto-loading,
local-media upload/rewrite for ``--remote``, HF_JOBS_KEYS stripping, argv round-trip,
and docstring argument extraction. Trivial helpers and per-subcommand parser smokes
are intentionally left out.
"""

import json
import subprocess
from argparse import Namespace
from types import SimpleNamespace

import pytest

from diffusers.commands.custom_blocks import CustomBlocksCommand
from diffusers.commands.run import (
    _INPUTS_MOUNT_ROOT,
    HF_JOBS_KEYS,
    _build_task_kwargs,
    _kwargs_to_argv,
    _maybe_upload_local_media,
    _parse_pipeline_kwargs,
    _resolve_dtype,
    _resolve_media_inputs,
)
from diffusers.commands.schema import _parse_docstring_args


class TestParsePipelineKwargs:
    def test_none_returns_empty(self):
        assert _parse_pipeline_kwargs(None) == {}

    def test_valid_json_object(self):
        assert _parse_pipeline_kwargs('{"prompt": "a cat", "steps": 50}') == {"prompt": "a cat", "steps": 50}

    def test_invalid_json_raises(self):
        with pytest.raises(SystemExit, match="must be valid JSON"):
            _parse_pipeline_kwargs('{"prompt": "unterminated')

    def test_non_object_raises(self):
        with pytest.raises(SystemExit, match="must decode to a JSON object"):
            _parse_pipeline_kwargs('["not", "an", "object"]')


class TestResolveDtype:
    def test_none_returns_auto(self):
        assert _resolve_dtype(None) == "auto"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown dtype"):
            _resolve_dtype("bogus-dtype")

    def test_known_dtype_returns_torch_dtype(self):
        import torch

        assert isinstance(_resolve_dtype("bf16"), torch.dtype)


class TestResolveMediaInputs:
    def test_string_image_and_video_get_loaded(self, monkeypatch):
        monkeypatch.setattr("diffusers.commands.run.load_image", lambda v: f"img({v})")
        monkeypatch.setattr("diffusers.commands.run.load_video", lambda v: [f"frame({v})"])
        kwargs = {"image": "url1", "control_video": "url2", "prompt": "leave me alone"}
        _resolve_media_inputs(kwargs)
        assert kwargs == {"image": "img(url1)", "control_video": ["frame(url2)"], "prompt": "leave me alone"}

    def test_non_string_values_pass_through(self, monkeypatch):
        monkeypatch.setattr(
            "diffusers.commands.run.load_image",
            lambda v: pytest.fail("load_image should not fire on pre-loaded values"),
        )
        kwargs = {"image": ["url1", "url2"]}
        _resolve_media_inputs(kwargs)
        assert kwargs == {"image": ["url1", "url2"]}


class TestUploadLocalMediaAndRewrite:
    def _fake_api(self):
        calls = SimpleNamespace(create_bucket=[], batch_bucket_files=[])
        api = SimpleNamespace(
            create_bucket=lambda bucket_id, exist_ok=False: calls.create_bucket.append((bucket_id, exist_ok)),
            batch_bucket_files=lambda bucket_id, add=None: calls.batch_bucket_files.append((bucket_id, add)),
        )
        return api, calls

    def test_urls_and_missing_kwargs_are_noop(self):
        api, calls = self._fake_api()
        assert _maybe_upload_local_media(Namespace(pipeline_kwargs=None, push_to="u/b"), api, "r") is False
        args = Namespace(pipeline_kwargs='{"image": "https://example.com/cat.png"}', push_to="u/b")
        assert _maybe_upload_local_media(args, api, "r") is False
        assert calls.batch_bucket_files == []
        assert json.loads(args.pipeline_kwargs)["image"] == "https://example.com/cat.png"

    def test_local_files_uploaded_and_paths_rewritten(self, tmp_path):
        cat = tmp_path / "cat.png"
        cat.write_bytes(b"png")
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"mp4")
        args = Namespace(
            pipeline_kwargs=f'{{"image": "{cat}", "video": "{clip}", "prompt": "grey"}}',
            push_to="user/bucket",
        )
        api, calls = self._fake_api()
        assert _maybe_upload_local_media(args, api, "run123") is True
        assert calls.create_bucket == [("user/bucket", True)]
        assert calls.batch_bucket_files == [
            (
                "user/bucket",
                [
                    (str(cat), "run123/inputs/image_cat.png"),
                    (str(clip), "run123/inputs/video_clip.mp4"),
                ],
            )
        ]
        rewritten = json.loads(args.pipeline_kwargs)
        assert rewritten["image"] == f"{_INPUTS_MOUNT_ROOT}/run123/inputs/image_cat.png"
        assert rewritten["video"] == f"{_INPUTS_MOUNT_ROOT}/run123/inputs/video_clip.mp4"
        assert rewritten["prompt"] == "grey"

    def test_key_prefix_prevents_basename_collision(self, tmp_path):
        a = tmp_path / "a" / "input.png"
        b = tmp_path / "b" / "input.png"
        a.parent.mkdir()
        b.parent.mkdir()
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        args = Namespace(pipeline_kwargs=f'{{"image": "{a}", "mask_image": "{b}"}}', push_to="u/b")
        api, calls = self._fake_api()
        _maybe_upload_local_media(args, api, "run")
        remote = [r for _, r in calls.batch_bucket_files[0][1]]
        assert "run/inputs/image_input.png" in remote
        assert "run/inputs/mask_image_input.png" in remote


class TestBuildTaskKwargs:
    def test_strips_hf_jobs_keys_and_falsy(self):
        args = Namespace(
            remote=True,
            flavor="a100-large",
            timeout="10m",
            format="auto",
            func=object(),
            model="x",
            dtype="bf16",
            revision=None,
            trust_remote_code=False,
            vae_tiling=True,
        )
        result = _build_task_kwargs(args)
        assert result == {"model": "x", "dtype": "bf16", "vae_tiling": True}

    def test_hf_jobs_keys_covers_regressions(self):
        for key in ("format", "remote", "func"):
            assert key in HF_JOBS_KEYS


class TestKwargsToArgv:
    def test_task_first_and_kebab_case_flag(self):
        argv = _kwargs_to_argv("run", {"cpu_offload": "model"})
        assert argv[0] == "run"
        assert "--cpu-offload" in argv
        assert argv[argv.index("--cpu-offload") + 1] == "model"

    def test_boolean_true_emits_flag_only(self):
        assert _kwargs_to_argv("run", {"vae_tiling": True}) == ["run", "--vae-tiling"]

    def test_list_repeats_flag(self):
        argv = _kwargs_to_argv("run", {"dependencies": ["torch", "accelerate"]})
        assert argv.count("--dependencies") == 2
        assert "torch" in argv and "accelerate" in argv


class TestParseDocstringArgs:
    def test_empty_and_no_args_section(self):
        assert _parse_docstring_args(None) == {}
        assert _parse_docstring_args("Just prose.\n\nReturns:\n    Nothing.\n") == {}

    def test_google_style_with_type_annotation(self):
        docstring = """Do something.

        Args:
            prompt (str): The prompt text.
            steps (int, optional): Steps to run.
        """
        assert _parse_docstring_args(docstring) == {"prompt": "The prompt text.", "steps": "Steps to run."}

    def test_multiline_description_joined(self):
        docstring = """Description.

        Args:
            prompt: The text prompt to guide
                image generation using diffusion
                sampling from noise.
        """
        result = _parse_docstring_args(docstring)
        assert "text prompt to guide" in result["prompt"]
        assert "sampling from noise" in result["prompt"]
        assert "\n" not in result["prompt"]


class TestCustomBlocksClassDiscovery:
    def test_finds_only_modular_pipeline_block_subclasses(self, tmp_path):
        block_py = tmp_path / "block.py"
        block_py.write_text(
            "class OtherBase:\n    pass\n"
            "class NotABlock(OtherBase):\n    pass\n"
            "class MyBlock(ModularPipelineBlocks):\n    pass\n"
        )
        cmd = CustomBlocksCommand.__new__(CustomBlocksCommand)
        results = cmd._get_class_names(block_py)
        assert results == [("MyBlock", "ModularPipelineBlocks")]

    def test_syntax_error_raises_value_error(self, tmp_path):
        block_py = tmp_path / "block.py"
        block_py.write_text("class Broken(:\n    pass\n")
        cmd = CustomBlocksCommand.__new__(CustomBlocksCommand)
        with pytest.raises(ValueError, match="Could not parse"):
            cmd._get_class_names(block_py)


def test_toplevel_help_lists_all_commands():
    result = subprocess.run(
        ["python", "-m", "diffusers.commands.diffusers_cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for cmd in ("env", "fp16_safetensors", "custom_blocks", "run", "schema", "skills"):
        assert cmd in result.stdout
