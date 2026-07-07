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
"""Essential unit tests for the non-``skills`` CLI commands.

One test per contract that would ship broken if regressed. Grouped by command.
"""

import subprocess
from types import SimpleNamespace

import pytest

from diffusers.commands.custom_blocks import CustomBlocksCommand
from diffusers.commands.run import (
    _kwargs_to_argv,
    _parse_pipeline_kwargs,
    _resolve_dtype,
    _resolve_media_inputs,
)
from diffusers.commands.schema import _parse_docstring_args


AVAILABLE_COMMANDS = ("env", "fp16_safetensors", "custom_blocks", "run", "schema", "skills")


class TestRunCommand:
    def test_parse_pipeline_kwargs(self):
        assert _parse_pipeline_kwargs('{"prompt": "a cat", "steps": 50}') == {"prompt": "a cat", "steps": 50}
        with pytest.raises(SystemExit, match="must be valid JSON"):
            _parse_pipeline_kwargs('{"prompt": "unterminated')
        with pytest.raises(SystemExit, match="must decode to a JSON object"):
            _parse_pipeline_kwargs('["not", "an", "object"]')

    def test_resolve_dtype_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown dtype"):
            _resolve_dtype("dummy-dtype")

    def test_resolve_media_inputs(self, monkeypatch):
        monkeypatch.setattr("diffusers.commands.run.load_image", lambda v: f"img({v})")
        monkeypatch.setattr("diffusers.commands.run.load_video", lambda v: [f"frame({v})"])
        kwargs = {"image": "url1", "control_video": "url2", "prompt": "text", "mask_image": ["pre", "loaded"]}
        _resolve_media_inputs(kwargs)
        assert kwargs == {
            "image": "img(url1)",
            "control_video": ["frame(url2)"],
            "prompt": "text",
            "mask_image": ["pre", "loaded"],  # non-string values pass through untouched
        }

    def test_kwargs_to_argv(self):
        argv = _kwargs_to_argv(
            "run", {"cpu_offload": "model", "vae_tiling": True, "dependencies": ["torch", "accelerate"]}
        )
        assert argv[0] == "run"
        assert "--cpu-offload" in argv and argv[argv.index("--cpu-offload") + 1] == "model"
        assert "--vae-tiling" in argv
        assert argv.count("--dependencies") == 2 and "torch" in argv and "accelerate" in argv


class TestSchemaCommand:
    def test_parse_docstring_args(self):
        docstring = """Description.

        Args:
            prompt (str): The prompt text
                wraps across multiple lines
                for readability.
            steps (int, optional): Steps to run.
        """
        result = _parse_docstring_args(docstring)
        assert result["steps"] == "Steps to run."
        assert "wraps across multiple lines" in result["prompt"]
        assert "\n" not in result["prompt"]


class TestCustomBlocksCommand:
    def test_class_discovery(self, tmp_path):
        block_py = tmp_path / "block.py"
        block_py.write_text(
            "class OtherBase:\n    pass\n"
            "class NotABlock(OtherBase):\n    pass\n"
            "class MyBlock(ModularPipelineBlocks):\n    pass\n"
        )
        cmd = CustomBlocksCommand.__new__(CustomBlocksCommand)
        assert cmd._get_class_names(block_py) == [("MyBlock", "ModularPipelineBlocks")]

        broken = tmp_path / "broken.py"
        broken.write_text("class Broken(:\n    pass\n")
        with pytest.raises(ValueError, match="Could not parse"):
            cmd._get_class_names(broken)


class TestCli:
    def test_toplevel_help_lists_all_commands(self):
        result = subprocess.run(
            ["python", "-m", "diffusers.commands.diffusers_cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        for cmd in AVAILABLE_COMMANDS:
            assert cmd in result.stdout


def _fake_bucket_api():
    calls = SimpleNamespace(create_bucket=[], batch_bucket_files=[])
    api = SimpleNamespace(
        create_bucket=lambda bucket_id, exist_ok=False: calls.create_bucket.append((bucket_id, exist_ok)),
        batch_bucket_files=lambda bucket_id, add=None: calls.batch_bucket_files.append((bucket_id, add)),
    )
    return api, calls
