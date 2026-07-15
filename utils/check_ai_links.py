# coding=utf-8
# Copyright 2026 The HuggingFace Inc. team.
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
"""
Checks that relative links in the `.ai/` agent docs work once the tree is installed outside a checkout.

`diffusers-cli skills install` copies `.ai/` to a standalone location and symlinks the skill directories
into the agent's skills directory. Relative links survive that only if they stay inside `.ai/`, so two
things must hold for every relative link:

  1. it resolves to a file that exists, and
  2. it stays within `.ai/` — a link to `../src/diffusers/...` reads fine in a checkout but points at
     nothing once installed, and the agent is told to read these files.

Link to GitHub instead when the target is outside `.ai/`.

Run with: python utils/check_ai_links.py
"""

import re
import sys
from pathlib import Path


PATH_TO_AI = Path(".ai")

# [text](target) — target captured. Fenced code blocks are stripped first; they contain
# call syntax like `attn.to_q(hidden_states)` that otherwise reads as a link.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def check_ai_links():
    errors = []

    for md in sorted(PATH_TO_AI.rglob("*.md")):
        body = CODE_FENCE_RE.sub("", md.read_text(encoding="utf-8"))

        for target in LINK_RE.findall(body):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue

            # Strip any #fragment before resolving.
            path = (md.parent / target.split("#")[0]).resolve()

            if not path.exists():
                errors.append(f"{md}: `{target}` does not exist.")
            elif PATH_TO_AI.resolve() not in path.parents:
                errors.append(
                    f"{md}: `{target}` points outside `.ai/`, so it breaks once the skills are installed "
                    f"outside a checkout. Link to it on GitHub instead."
                )

    if errors:
        raise ValueError("Broken links in the `.ai/` agent docs:\n" + "\n".join(f"- {e}" for e in errors))


if __name__ == "__main__":
    try:
        check_ai_links()
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
