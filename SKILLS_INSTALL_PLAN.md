# Plan: `diffusers-cli skills install`

Make the `.ai/` agent docs + skills usable from any directory, not just a
`diffusers` checkout — the model-author-in-their-own-research-repo case.

## Target UX

```
diffusers-cli skills install                  # install (no-op if already present)
diffusers-cli skills install -U               # re-fetch and overwrite
diffusers-cli skills install --version 0.39.0 # errors: that release predates .ai/skills
diffusers-cli skills list                     # what's installed, from which ref
diffusers-cli skills uninstall
```

---

## What's actually at each ref

`.ai/` shipped in the `v0.38.0-release` branch but never in the published wheel. Checked against the
tags:

| ref | `.ai/` files | skills present |
|---|---|---|
| `v0.36.0`, `v0.37.0` | 0 | — |
| `v0.38.0` | 9 | `model-integration`, **`parity-testing`** |
| `v0.39.0` | 8 | `model-integration` (+`pitfalls.md`), **`self-review`** |
| `main` | 8 | `model-integration`, `self-review` |

Three consequences:

1. **The `--version` error boundary is `<= 0.37.0`, not 0.39.0.** The sketch's example is right in
   mechanism, wrong in constant — `--version 0.39.0` fetches the `v0.39.0` tag and *succeeds*. The
   version that errors is `0.37.0`.
2. **The skill set churns across versions.** `parity-testing` existed at 0.38 and is gone by 0.39;
   `self-review` appeared at 0.39. So `-U` cannot just overwrite directories — someone who installed
   at 0.38 has a `parity-testing` skill their agent still loads, describing a workflow that no longer
   exists upstream. `-U` has to *reconcile the set* and delete what's been dropped. This is why the
   install needs a manifest rather than being a pure copy, and it's a case I'd have missed entirely
   without looking at the tags.
3. **The CLI and the bundle ship in lockstep, which settles the bundling question below.** The `skills`
   command first exists in whatever release adds it (0.40+), so no 0.38/0.39 user can run it anyway.
   There's no version of diffusers that has the command but lacks the bundle — the skew scenario that
   would make bundling awkward can't occur. Bundling only ever needs to serve 0.40+, and GitHub fetch
   covers the older tags for anyone who explicitly asks for them.

## The blocking problem: skills are not self-contained

This is the part that has to be designed before any installer is worth writing.

`.ai/skills/model-integration/SKILL.md` links out of its own directory:

| Reference in SKILL.md | Resolves in-repo to | Resolves post-install to |
|---|---|---|
| `[models.md](../../models.md)` | `.ai/models.md` | `~/.claude/models.md` — missing |
| `[modular.md](../../modular.md)` | `.ai/modular.md` | `~/.claude/modular.md` — missing |
| `[pipelines.md](../../pipelines.md)` | `.ai/pipelines.md` | `~/.claude/pipelines.md` — missing |

`.ai/skills/self-review/SKILL.md` is worse: it doesn't link, it *instructs* — "`.ai/review-rules.md`
is the canonical rubric — read it", and "if models were touched, also read `.ai/models.md`". Outside a
checkout those paths don't exist, and the agent will either hallucinate the rubric or silently skip it.
A self-review that quietly reviews against nothing is worse than no installer at all.

So `cp -r .ai/skills ~/.claude/skills` produces two skills whose every reference is broken. The shared
docs (`models.md`, `modular.md`, `pipelines.md`, `review-rules.md`) are part of the payload, and the
question is how they travel.

### Recommendation: restructure the repo so the copy is dumb

Give each skill a `references/` directory in-repo, and point the skills at it:

```
.ai/
  models.md  modular.md  pipelines.md  review-rules.md   # canonical, referenced by AGENTS.md
  skills/
    model-integration/
      SKILL.md            # links become references/models.md
      pitfalls.md
      references/         # -> ../../models.md, ../../modular.md, ../../pipelines.md
    self-review/
      SKILL.md            # prose becomes references/review-rules.md
      references/         # -> ../../review-rules.md, ../../models.md, ...
```

Then install is `shutil.copytree` of one directory per skill, with zero path rewriting, and the
in-repo and installed layouts are byte-identical — the thing that keeps this from drifting.

Two ways to populate `references/`:

- **Git symlinks** (`ln -s ../../../models.md`), extended to the existing `make claude` target. Cheap,
  no duplication, one source of truth. Costs: Windows checkouts need `core.symlinks` (the repo already
  ships `CLAUDE.md -> .ai/AGENTS.md`, so this bar is already set), and the installer must
  `copytree(symlinks=False)` to materialize real files at the destination.
- **A `make sync-ai` target** that copies and a `utils/check_ai_docs.py` that CI runs to assert the
  copies match. Windows-safe, but adds a fourth thing to the `make fix-copies` family and a new class
  of "you forgot to run make sync-ai" CI failure.

I'd go with symlinks, matching the `CLAUDE.md` precedent already in the tree.

### The alternative: rewrite links at install time

Keep `.ai/` as-is and have the installer regex `../../models.md` → `references/models.md` while
copying. This is what you'd reach for to avoid touching the repo layout, and it's the option I'd
argue against: it puts a transformation between what a contributor reads in-repo and what a user's
agent actually reads, and the rewrite table silently rots the moment someone adds a doc or a link
style it doesn't match. The self-review skill's *prose* references (`read .ai/review-rules.md`) can't
be regexed at all without guessing at sentences.

---

## Decisions to make

### 1. Default source — bundled vs `main` (needs your call)

The sketch in the thread says `install` always pulls from `main`. That's implementable (GitHub
tarball, ~72K), but it's worth naming the tradeoff before it's locked in, because it conflicts with
the offline point raised in the thread:

|  | Bundled in the wheel | Fetched from `main` |
|---|---|---|
| Offline / air-gapped | works | fails |
| Matches your installed `diffusers` | always | no — skills can describe APIs your version doesn't have |
| Freshness | pinned to release | always current |
| `--version 0.39.0` | already have it, trivially | tarball of `v0.39.0`, error if no `.ai/skills` |
| Cost | build step (see below) | network dep in the CLI |

The version-skew row is the one that bites: a researcher on `diffusers==0.40` who installs skills from
`main` gets integration guidance for APIs that don't exist in their env, and the failure is a confused
agent rather than an error. (Note this is about skew between the *installed package* and the *skills*,
not about old releases — the lockstep point above means no one can run this command on 0.38/0.39.)

**My recommendation:** bundle as the default, fetch as opt-in.

- `skills install` → the bundled copy, matching the installed version. No network, always works.
- `skills install --version main` (or `--dev`) → GitHub tarball of `main`.
- `skills install --version 0.39.0` → GitHub tarball of `v0.39.0`; error if `.ai/skills` isn't in it.
- `-U` → re-resolve the same source and overwrite.

This satisfies "offline install... we could bundle though? They're just markdowns" and keeps the
`--version` flag meaningful. If you'd rather match the original sketch exactly, flip the default to
`main` and keep bundled as `--version installed` — the installer code is the same either way,
only the default ref changes.

**Bundling requires a build step.** `.ai/` sits at the repo root, outside `package_dir={"": "src"}`,
so `find_packages("src")` won't see it. Options: a `build_py` subclass in `setup.py` that copies
`.ai/` → `src/diffusers/_ai_skills/` at build time (+ `package_data`, + `.gitignore`), or move `.ai/`
under `src/diffusers/` and leave a root symlink. The former keeps the contributor-facing path stable;
I'd do that, and add a wheel-contents assertion to the release checklist since a silently-empty
`package_data` glob is the classic way this breaks.

### 2. Install scope

The complaint is "launch claude outside the diffusers folder", so the default should work no matter
where the agent starts: **user-level `~/.claude/skills/`**, with `--project` for `./.claude/skills/`
when someone wants it committed to their research repo.

### 3. Naming and frontmatter

Claude Code matches the skill directory name; the `model-integration/` directory currently declares
`name: integrating-models` in its frontmatter — inconsistent in-repo, and actively a problem once
installed. Also, unprefixed `self-review` in a user's global skill dir is a name collision waiting to
happen.

Namespace both at the source: rename the directories to `diffusers-model-integration/` and
`diffusers-self-review/` and set frontmatter `name:` to match. Doing it in-repo rather than at install
time keeps the "installer is a dumb copy" property.

### 4. Codex

`make codex` wires the same skills to `.agents/skills`. `--agent claude|codex|all` (default `claude`)
covers it; the payload is identical, only the destination differs.

---

## "How would the agents know to run this?"

Straight answer: they wouldn't, and no amount of CLI design fixes that. `skills install` is a **human**
step, same as every other CLI-installed skill. The order of operations from the thread is right:

1. `pip install diffusers`
2. `diffusers-cli skills install`
3. Point the agent at the task

What we can do is make step 2 discoverable (README + `contribution.md` + a line in the new-model issue
template) and make the install print the next step. What actually removes the step is a **Claude Code
plugin** — a marketplace entry installs skills without diffusers being pip-installed at all. That's
YiYi's suggestion and it's the better long-term answer for agent-first users; it's also a separate
distribution channel with its own release story. The CLI installer is the right first move because it
works for the pip-installing researcher today, and a plugin can wrap the same `.ai/` tree later. Both
read the same restructured source, so the work here isn't thrown away.

---

## Implementation sketch

New `src/diffusers/commands/skills.py`, registered in `diffusers_cli.py` alongside the existing three
commands, following the `BaseDiffusersCLICommand` pattern:

```
SkillsCommand
  install   --version REF  -U/--upgrade  --agent claude|codex|all  --project
  list
  uninstall
```

- **Resolve source.** No `--version` → bundled `diffusers/_ai_skills/`. Otherwise GitHub tarball:
  `main` → `refs/heads/main.tar.gz`, `X.Y.Z` → `refs/tags/vX.Y.Z.tar.gz`. `requests` is already in
  `install_requires`, no new dep.
- **Validate.** Tarball has no `.ai/skills/` → `"diffusers 0.37.0 predates the agent skills; they
  landed in 0.38.0. Use --version main or a newer release."` Per CLAUDE.md: concise error, no
  fallback to a different ref. **The boundary is `<= 0.37.0`, not 0.39.0** — see below.
- **Copy.** Each `.ai/skills/<name>/` → `<dest>/<name>/`, `symlinks=False`. Existing dir + no `-U` →
  report and exit; `-U` → replace.
- **Record + reconcile.** `<dest>/<name>/.diffusers-skill.json` with `{ref, version, installed_at}`.
  `-U` **reconciles the set** against the manifest — installs new, overwrites changed, and *removes
  skills that no longer exist at the target ref*. Not optional; see the churn note below.
- **Print next steps.** Where it went, which skills, how to invoke.

### Tests

`tests/others/test_skills_command.py` — there's no existing test for the CLI commands, so this
establishes the pattern:

- bundled install into a `tmp_path` dest, asserting `SKILL.md` **and** `references/` land
- install is a no-op without `-U`, overwrites with it
- `--version 0.39.0` raises with the readable message (mock the fetch — no network in CI)
- **link integrity: every relative markdown link in every installed `SKILL.md` resolves to a file that
  exists.** This is the test that would have caught the `../../models.md` problem, and it should run
  against the repo tree too so the skills can't drift back into non-self-contained.

### Docs

`AGENTS.md` setup section (currently `make claude` only — add the outside-the-repo path), plus README
and `contribution.md` where skills are already mentioned.

---

## Order of work

1. Restructure `.ai/` for self-containment (`references/`, rename dirs, fix frontmatter), extend
   `make claude` / `make codex`. **Nothing else works before this.**
2. Link-integrity check over the repo tree; wire into `make quality`.
3. Bundle `.ai/` into the wheel (build step + `package_data`) — if we go with bundled-by-default.
4. `skills.py` + registration.
5. Tests.
6. Docs.

Steps 1–2 are independently valuable: they're what make the skills coherent to *any* agent, and they'd
be worth doing even if the installer never shipped.

## Open questions

1. **Bundled or `main` by default?** The version-skew vs freshness call above. Everything else is
   settled by it.
2. **Symlinks or a checked-in copy** for `references/`? Symlinks match the existing `CLAUDE.md`
   precedent; a copy is Windows-safe at the cost of a sync check.
3. ~~Which release first shipped `.ai/skills`?~~ **Resolved — 0.38.0.** See "What's actually at each
   ref" above. My earlier claim that 0.39.0 lacked `.ai/` was wrong; both 0.38.0 and 0.39.0 tags have
   it. One sub-question remains: **what does `--version` resolve against** — the git tag (`0.38.0` and
   `0.39.0` both work) or the published wheel (nothing before 0.40 works)? The sketch's "`--version
   0.39.0` errors" is only true under the wheel reading. I'm assuming git tags; flag if you meant
   wheels.
4. **Plugin now or later?** Recommending later, but if agent-first users are the priority it changes
   the sequencing — not the `.ai/` restructure, which both paths need.
