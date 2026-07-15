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
3. **The skill set is the fastest-moving thing here**, which is the case for tracking `main` (Decision
   1). Between two releases the set turned over almost completely. Skills pinned to a release would be
   stale for most of that release's life.

## Self-containment: the Makefile already has the answer

**Retracted.** An earlier draft of this plan called this "the blocking problem" and proposed
restructuring `.ai/` to give each skill a `references/` directory. That was wrong, and `make claude`
is why.

`make claude` symlinks the **directory**: `.claude/skills -> ../.ai/skills`. The `.ai/` tree stays
intact underneath, so a skill's `../../models.md` still lands on a real file. POSIX resolves `..`
*after* symlink traversal, in the kernel — so even reading through the symlinked path,
`.claude/skills/model-integration/../../models.md` opens `.ai/models.md`. Symlinked skill entries are
explicitly supported, not a trick we're getting away with — per the skills docs, *"a `<skill-name>`
entry … can be a symlink to a directory elsewhere on disk. Claude Code follows the symlink and reads
`SKILL.md` from the target directory."* Verified in the checkout, and verified for the per-skill
symlink an installer would actually create:

```
~/.claude/skills/diffusers-model-integration -> ~/.diffusers/ai/skills/model-integration
  SKILL.md                              readable through the link
  ../../models.md                       -> ~/.diffusers/ai/models.md          EXISTS
  pathlib .resolve() agrees             (resolve() follows the link, then normalizes)
```

So the links were never the problem — the *tree* is. Relative links work anywhere as long as the whole
`.ai/` tree ships together and the skill dirs are symlinked into place rather than copied out of it.
No restructure, no link rewriting, no `references/` duplication, and no drift between what a
contributor reads in-repo and what a user's agent reads.

### Recommendation: the installer is `make claude` for an arbitrary directory

Ship the whole `.ai/` tree to a stable home, then link the skills into the agent's skill dir — exactly
the shape the Makefile already uses, just not rooted in a checkout:

```
~/.diffusers/ai/                            # the whole tree, as it exists in-repo
  models.md  modular.md  pipelines.md  review-rules.md  AGENTS.md
  skills/model-integration/  skills/self-review/

~/.claude/skills/diffusers-model-integration -> ~/.diffusers/ai/skills/model-integration
~/.claude/skills/diffusers-self-review       -> ~/.diffusers/ai/skills/self-review
```

Per-skill links rather than symlinking `skills/` wholesale, since the user's skill dir holds other
skills too. `uninstall` unlinks; `-U` replaces the tree and re-reconciles the links.

### What actually is broken: self-review's prose paths

One real issue survives, much narrower than I claimed. `model-integration` uses relative markdown
links (`../../models.md`) and travels fine. `self-review` doesn't link — it *instructs*, using
**cwd-relative repo paths**: "`.ai/review-rules.md` is the canonical rubric — read it", and "also read
`.ai/models.md`, `.ai/pipelines.md`, or `.ai/modular.md`". Those resolve against wherever the agent was
launched. In a checkout that's the repo root and it works; in a researcher's own repo there's no
`.ai/`, so the agent either hallucinates the rubric or skips it silently.

Fix is a one-file edit, not a restructure: make those references relative to `SKILL.md`
(`../../review-rules.md`), which then resolves through the symlink like everything else. Worth asking
whether `self-review` should even install outside a checkout — it operates on a diffusers PR diff, so
the researcher-in-their-own-repo case is really about `model-integration`. Scoping the default install
to the skills that make sense standalone is an option; I'd install both and let the description
disambiguate.

### POSIX only

Decided: symlinks, no Windows fallback. `os.symlink` needs Developer Mode or admin on Windows, and the
alternatives (junctions, or copying the tree per skill dir) exist only to serve a platform that isn't
in scope. Per CLAUDE.md — no fallback paths "just in case". If `os.symlink` raises, that surfaces as
the error it is.

---

## Decisions

### 1. Default source — **`main`** (decided; bundling deferred)

Matches the original sketch: `install` fetches `.ai/` from `main` over the network. Bundling into the
wheel is set aside for now.

- `skills install` → GitHub tarball of `main`.
- `skills install -U` → re-fetch `main` and reconcile.
- `skills install --version 0.38.0` → tarball of tag `v0.38.0`; errors at `<= 0.37.0`.

What this buys, beyond the smaller diff: **the skills decouple from the release cycle.** A skill fix
lands on `main` and users get it with `-U` that day, no release needed. Given how fast the skill set
churns (finding 2 above — near-total turnover between 0.38 and 0.39), that freshness is worth more than
I credited when I argued for bundling. It also drops the whole `build_py` / `package_data` /
wheel-assertion apparatus, which was the most fragile part of the plan and the part that failed open.

Two things it costs, worth naming so they're deferred rather than forgotten:

- **No offline install.** This was raised in the thread ("Offline install is an issue since we don't
  bundle skills with the package. But we could though?") and is now a known gap, not an oversight. Air-
  gapped or flaky-network users get an error.
- **Skills can drift ahead of the installed package** — someone on an old diffusers pulling `main` gets
  guidance for APIs their version lacks. Left alone this degrades into a confused agent rather than a
  clean error, so it gets a validation check: **Decision 5**.

Both are addressed by bundling later, and nothing here forecloses it: the resolver is the same code
with a different default, so adding a bundled source is a new branch in one function, not a rework.

### 2. Install scope

The complaint is "launch claude outside the diffusers folder", so the default should work no matter
where the agent starts: **user-level `~/.claude/skills/`**, with `--project` for `./.claude/skills/`
when someone wants it committed to their research repo.

### 3. Naming — prefix the symlink, don't rename anything in-repo

Per the Claude Code skills docs, the **directory name is canonical for invocation**; frontmatter
`name:` is only "the display label shown in skill listings". They don't have to match.

That kills the in-repo rename an earlier draft proposed. A symlink's name is independent of its target,
so the installer alone decides the installed identity:

```
~/.claude/skills/diffusers-model-integration -> ~/.diffusers/ai/skills/model-integration
```

Repo keeps `model-integration/`; the user gets `/diffusers-model-integration`. No renames, no link
churn in `AGENTS.md`, and the installer stays a dumb copy + link.

**Why prefix at all:** a global install shares `~/.claude/skills/` with everything else the user has.
The risk isn't Claude Code's shadowing (documented, predictable: personal > project) — it's the
filesystem. Installing a bare `self-review/` would **overwrite a user's own skill of that name**. The
prefix makes that essentially impossible, and makes provenance obvious in a skill listing.

**`name: integrating-models` in `model-integration/` is cosmetic**, not a bug — display label only. It
is still confusing that the label and the invocation name differ, so aligning it is worth doing as
tidying; it just isn't load-bearing and isn't a prerequisite for anything.

*Undocumented, worth a glance during implementation:* the docs don't specify character/length
constraints on `name:`. Nothing here pushes those limits, so it's not a blocker.

### 4. Codex

`make codex` wires the same skills to `.agents/skills`. `--agent claude|codex|all` (default `claude`)
covers it; the payload is identical, only the destination differs.

### 5. Version-drift check — error, don't warn

Refuse to install skills that are too far ahead of the installed diffusers, rather than letting the
agent discover the mismatch by writing code against APIs that aren't there. Per CLAUDE.md: concise
error for the unsupported case, no silent correction.

**Getting the skills' version costs nothing extra.** The tarball we already fetch contains
`src/diffusers/__init__.py`, so read `__version__` straight from it at that ref — no new metadata file
to maintain, no risk of it going stale. `packaging.version` is already used across
`src/diffusers/utils/`.

**The threshold is the whole design, and it can't be "must match".** Main declares `0.40.0.dev0` while
the newest release is `0.39.0` (verified) — main is *always* one minor ahead of the latest release, by
construction. So an exact-match or any-drift-errors rule would reject the modal user: someone who
pip-installed the newest release and ran `skills install`. The default path would error for nearly
everyone, nearly always.

The rule that isn't arbitrary:

> **error when `installed.minor < skills.minor - 1`**

The `- 1` isn't a fudge factor — since main sits exactly one minor ahead of the newest release, "at most
one minor behind the skills" is precisely "you are on the latest release or newer." Concretely, with
main at `0.40.0.dev0`:

| installed | skills ref | | why |
|---|---|---|---|
| `0.39.0` (latest release) | `main` (0.40.0.dev0) | ok | one behind == on the latest release |
| `0.40.0.dev0` (source checkout) | `main` | ok | equal |
| `0.38.0` | `main` | **error** | two minors back |
| `0.40.0` | `--version 0.38.0` | ok | skills *behind* package, and explicitly pinned |

Applied uniformly — an explicit `--version` doesn't bypass it. Same resolved ref must mean same
validation, otherwise `install` and `install --version main` behave differently despite fetching the
identical tarball, which is indefensible to document.

**`--force` overrides the drift check** (warns instead of raising). An earlier draft argued against it
on the CLAUDE.md "no options just in case" rule; that was wrong, because this isn't speculative:

- **A real workflow needs it.** A model author whose env pins an older diffusers — often not their
  choice, it's their reference implementation's constraint — but who is contributing *to main* and will
  open a PR there. For them the "pin to your version" remedy is actively harmful: it hands them stale
  conventions for a main-targeted PR.
- **This check is a heuristic over prose, not a correctness invariant.** Skills are guidance, not
  API-coupled code; a two-minor gap may be entirely fine for `model-integration`. Minor-version
  proximity is a proxy, and proxies are wrong sometimes.
- **The operation is non-destructive.** It writes markdown into a skills directory. The failure being
  prevented is a confused agent, not corruption, and `uninstall` reverses it.

An override that can be cargo-culted into scripts is the real cost, so make the outcome *visible*
rather than silent: `--force` warns loudly, and the manifest records `forced: true` alongside the ref,
so `list` can show `skills 0.40.0.dev0 / diffusers 0.38.0 (forced)`. When someone later reports the
agent hallucinating APIs, that line is the first thing to look at.

**`--force` applies only to the drift check, never to check 1.** If a ref has no `.ai/skills/` there is
nothing to install — forcing can't conjure files, so that stays a hard error. Worth stating because
"`--force` skips validation" is exactly the assumption someone will make.

The `<= 0.37.0` boundary keeps its own message, since there the answer isn't "pin to your version" (no
skills exist at 0.37) but "upgrade or use `main`".

---

## "How would the agents know to run this?"

Straight answer: they wouldn't, and no amount of CLI design fixes that. `skills install` is a **human**
step, same as every other CLI-installed skill. The order of operations from the thread is right:

1. `pip install diffusers`
2. `diffusers-cli skills install`
3. Point the agent at the task

What we can do is make step 2 discoverable (README + `contribution.md` + a line in the new-model issue
template) and make the install print the next step.

A Claude Code plugin was raised in the thread as an alternative channel. **Out of scope** — and worth
noting it wouldn't remove the manual step anyway, only change it from `diffusers-cli skills install` to
`/plugin marketplace add`. It would read the same `.ai/` tree, so nothing here forecloses it later.

---

## Implementation sketch

New `src/diffusers/commands/skills.py`, registered in `diffusers_cli.py` alongside the existing three
commands, following the `BaseDiffusersCLICommand` pattern:

```
SkillsCommand
  install   --version REF  -U/--upgrade  --force  --agent claude|codex|all  --project
  list
  uninstall
```

- **Resolve source.** Always a GitHub tarball — **git refs are the only option, since no published
  wheel ships `.ai/`**:
  - no `--version`, or `--version main` → `refs/heads/main.tar.gz`
  - `X.Y.Z` → `refs/tags/vX.Y.Z.tar.gz` (immutable, unambiguous)
  - Release branches are named `vX.Y.0-release`. They currently match their tags exactly (verified:
    zero commits of drift for both 0.38.0 and 0.39.0), so tags are the better resolution — a branch
    moves under you as patches land. If "latest patch on the 0.38 line" is wanted, that's a distinct
    spelling (`--version 0.38` → `refs/heads/v0.38.0-release.tar.gz`); worth adding only if you want it.

  `requests` is already in `install_requires`, no new dep.
- **Validate — two checks, both hard errors** (Decision 5):
  1. *Skills exist at the ref.* No `.ai/skills/` in the tarball → `"diffusers 0.37.0 predates the agent
     skills; they landed in 0.38.0. Use --version main or a newer release."` **The boundary is
     `<= 0.37.0`, not 0.39.0.**
  2. *Skills aren't too far ahead of the installed package.* Read `__version__` from the tarball's
     `src/diffusers/__init__.py`; error when `installed.minor < skills.minor - 1` →
     `"main's skills target diffusers 0.40.0.dev0 but 0.38.0 is installed. Upgrade diffusers, pin the
     skills with --version 0.38.0, or pass --force to install anyway."`

  No fallback to a different ref in either case. `--force` downgrades check 2 to a warning; check 1
  stays fatal regardless.
- **Place.** Whole `.ai/` tree → `~/.diffusers/ai/` (`copytree`), then per-skill symlink into
  `<dest>/diffusers-<name>`. Existing install + no `-U` → report and exit; `-U` → replace.
- **Record + reconcile.** `<dest>/<name>/.diffusers-skill.json` with `{ref, version, installed_at}`.
  `-U` **reconciles the set** against the manifest — installs new, overwrites changed, and *removes
  skills that no longer exist at the target ref*. Not optional; see the churn note below.
- **Print next steps.** Where it went, which skills, how to invoke.

### Tests

`tests/others/test_skills_command.py` — there's no existing test for the CLI commands, so this
establishes the pattern:

- install into a `tmp_path` dest from a fixture tarball, asserting the tree lands and the skill
  symlinks point into it
- install is a no-op without `-U`, overwrites with it; `-U` drops skills absent at the new ref
- `--version 0.37.0` raises with the readable message (mock the fetch — no network in CI)
- **drift check, parametrized over the table in Decision 5** — including the two that must *pass*
  (latest-release-installed + `main`, and skills-behind-package). A drift check that rejects the modal
  user is the likely failure mode, so pin the passing cases, not just the erroring one.
- `--force` installs through a drift error and records `forced: true`; `--force` does **not** get past
  a ref with no `.ai/skills/`
- **link integrity: every relative markdown link in every installed `SKILL.md` resolves to a file that
  exists, resolved *through the symlink* from the installed location** — not from the repo. Reading
  through the link is exactly what the agent does, and it's what makes this whole design work, so it's
  the thing to pin. Run it against the repo tree too, so a future skill can't add a link that only
  works in a checkout.

### Docs

`AGENTS.md` setup section (currently `make claude` only — add the outside-the-repo path), plus README
and `contribution.md` where skills are already mentioned.

---

## Order of work

1. Fix `self-review`'s cwd-relative `.ai/*.md` prose paths → relative to `SKILL.md`. Small,
   self-contained, and a real bug today. (No dir renames — the installer names the symlink; see
   Decision 3. Aligning `model-integration`'s frontmatter `name:` is optional tidying.)
2. Link-integrity check (resolved through a symlink, not from the repo root); wire into `make quality`.
3. `skills.py` + registration.
4. Tests.
5. Docs.

Step 1 is worth doing even if the installer never ships — it's a real bug for anyone running
`self-review` from outside the repo root today. The rest is no longer gated on a restructure, so this
is a smaller change than the first draft implied.

## Open questions

*None — all resolved. Kept below as a record of what was decided and why.*

1. ~~Bundled or `main` by default?~~ **`main`**; bundling deferred, with the offline gap and the
   package/skills drift accepted as known costs. See Decision 1.
2. ~~Symlinks or a checked-in copy for `references/`? Is Windows in scope?~~ **Both resolved.** No
   `references/` needed — the Makefile's directory-symlink approach already preserves relative links,
   and the installer just mirrors it. POSIX only, no Windows fallback.
3. ~~Which release first shipped `.ai/skills`? What does `--version` resolve against?~~ **Both
   resolved.** Skills landed in 0.38.0, so the error boundary is `<= 0.37.0`. And since no published
   wheel ships `.ai/`, the wheel reading is off the table — `--version` resolves git refs (tags;
   release branches are `vX.Y.0-release` and currently identical to their tags). Consequence worth
   restating: **`--version 0.39.0` will not error** — the sketch's example needs the constant changed
   to `0.37.0`.
4. ~~Plugin now or later?~~ **Out of scope.**
