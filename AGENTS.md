# AGENTS.md

Goal: rebuild *The Battle for Middle-earth II* (`game.dat` 1.06) as clean, readable, maintainable C++ that
compiles 1-to-1 with Visual C++ 7.1 (`cl.exe 13.10.3077`) to the original machine code, so the engine can be
patched and extended.

For multi-agent swarm orchestration and copy-paste prompts, also read [docs/swarm.md](docs/swarm.md) and
[docs/agents.md](docs/agents.md).

## Where things live

| Path | What it is |
|---|---|
| `src/` | source files (`src/unk/O<addr>.cpp` are TUs whose original filename is not yet proven; never add `src/Code/Tools/` — exporter/plugin code is not linked into `game.dat` and is refused by G1) |
| `include/` | shared headers (`include/Draft/` holds temporary unverified layouts until promoted to a real header) |
| `config/map.json` | the ledger: every function and data item of `game.dat`, with RVA, mangled name, owner TU, and status |
| `expected/` | generated check records; never edit in contributor PRs |
| `baseline/game.dat` | original 1.06 binary (`VA = RVA + 0x400000`); disassemble here to verify exact behavior |
| `reference/` | Zero Hour source (predecessor game); reference for names/structure only, never compiled |
| `worklist/` | prioritized work queues regenerated from `main` |
| `docs/` | source-quality rules, recipes, naming conventions, swarm playbook, and recorded hard cases |
| `tools/`, `ci/` | local source gate (`tools/gates/gate.py`), local judge docs (`tools/README.md`), and CI; never edit in source PRs |

## Live auto-landing lanes (zero-human merge for eligible Ready PRs)

Eligible Ready contributor PRs — both source-only PRs and supported header PRs — auto-land on `main` without human approval once every required check passes:

1. **Fastest lane — source-only (`src/`):**
   - 1–8 eligible `src/` files (G6 caps substantive edits at 3 source files) affecting `<= 8` TUs.
   - Admitted automatically on Ready PRs within the narrow daily budget (`BFME_ADMISSION_DAILY_PER_AUTHOR` / `BFME_ADMISSION_DAILY_TOTAL`; same-day per-commit deduplication avoids extra quota burn when `main` advances).
   - Auto-merges once `BFME II / source rules` (G1–G8) and `BFME II / Boat` pass with zero regressions.
2. **Clean header lane (`include/`):**
   - Ready fork PRs changing `include/` (up to 60 paths under `src/` and `include/`) are admitted automatically within the wide daily budget (`BFME_ADMISSION_WIDE_DAILY_PER_AUTHOR` / `BFME_ADMISSION_WIDE_DAILY_TOTAL`, H10).
   - Auto-merges without human approval once `BFME II / Boat` (wide comparison, H1) and `BFME II / Header rules` (H1–H10) pass, including:
     - Adding missing member-function declarations whose compiled symbol matches the ledger (H2),
     - Access-specifier updates (`public:`/`protected:`/`private:`),
     - Plain non-inline, non-pure virtual overrides of existing base virtual slots (without default arguments or private-base visibility errors, proven via VC7.1 vcall-thunk matching under H5),
     - Plain abstract classes without virtual inheritance (H5),
     - Moved/added data members proven by a newly matched function that accesses them (H5 typed-access witness).
   - Edits outside H5's automated subset (e.g., `> 4` changed classes per header, unproved layout/size/base changes, virtual-base abstract classes, private-base overrides, inline/pure/default-arg virtual overrides, or `Held: ledger request` items) hold safely for a maintainer.
3. **Allowed SDK opt-in lines (G2):**
   - Adding `// dxsdk` or `// platformsdk` at the top of an existing non-vendored `.c`/`.cpp` file is allowed when needed for DirectX/Win32 headers (no duplicates, LF line endings, no trailing whitespace).
   - Never add or edit `// cl*`, `// psdk`, `// stlport*`, `// msvcstl`, `// bfmealloc_alloconly`, or `// FUNCTION:` / `// DATA:` annotations.

## Work selection & collision-free claiming

1. Always branch from current upstream `main` (`main` moves continuously).
2. **Search upstream open PRs before choosing** (claims live in PR bodies as well as titles):
   ```sh
   gh pr list -R Open-BFME/BFME2-Rebuild --state open --search "<RVA or file or header>"
   ```
   Also `git grep` `src/` and `include/` for the target RVA — `worklist/` snapshots can lag `main`.
3. Spread picks across the queue instead of piling onto row 1:
   - **High-yield `.cpp` rows:** `worklist/files.tsv` (few missing functions, real header exists) or `worklist/unwritten_with_zero_hour_counterpart.tsv` (`ready_at_snapshot=yes`, `reference_kind=def`, `body_check=none-found`, small `size_bytes`).
   - **Header & Draft work:** `worklist/draft_headers_in_use.tsv` (create permanent header under `include/`, move all includers, delete the `include/Draft/` file) or add missing declarations to an existing shared header.
   - **Blockers & ledger issues:** file an `Evidence:`, `Path:`, or `Ledger:` issue, or record a verified compiler/layout blocker in `docs/hard_rows.md`.
4. **Claim before writing code:** open a Draft PR with `Claim: src/path/File.cpp`, `Claim: include/Draft/X.h`, or `Claim: 0x00ABCDEF` (or assign disjoint file slices in your swarm planner) so two agents never duplicate work.
5. **One logical item per PR** (G6: at most 3 substantive source files, except Draft promotions).

## Core quality & retail-accuracy rules

- **Ground truth is `baseline/game.dat`:** disassemble at `VA = RVA + 0x400000` (`objdump -d -M intel --start-address=<VA> --stop-address=<VA+size> baseline/game.dat`). Cross-check Zero Hour under `reference/` for names/structure, but whenever Zero Hour and `baseline/game.dat` disagree, **follow `baseline/game.dat`**.
- **Evidence-backed names only:** use the existing class/header spelling currently on `main` (never invent duplicate types; for example, match the exact class name declared in `include/WWMath/Matrix4.h` and `include/WW3D2/DX8Wrapper.h` on the current commit). `Code/Tools/` (`max2w3d`, `pluglib`, etc.) is exporter/plugin code never linked into `game.dat` and is refused by G1; runtime WW3D2 animation channels belong in `WW3D2/motchan.h` (`BitChannelClass`).
- **Header discipline:** put shared declarations in the proper header under `include/` and keep layouts faithful to retail. When adding or editing an inline or inherited virtual method, cite the retail vtable slot in the commit message / PR evidence.
- **Zero regressions & definition of done:** every existing matched function in affected TUs must keep matching; a function is done only when clean C++ compiles under Boat to match retail bytes and relocations and passes all gates.

## Fast, zero-waste engineering loop

1. **Write clean, idiomatic C++:**
   - Place the body directly below the owning `// FUNCTION:` line in the file `config/map.json` names.
   - Use real engine types and shared headers; never use inline asm, `__emit`, `#pragma optimize`, `void *`/integer stand-ins for known types, duplicate class/global definitions, or address-named identifiers/comments (`rva00...`, `sub_...`, dates, author tags).
2. **Commit on top of fresh `main` and run the local gate BEFORE pushing:**
   ```sh
   python3 tools/gates/gate.py . <upstream-main-ref> HEAD --json /tmp/gate-<slug>.json
   ```
   `gate.py` inspects committed Git trees (it ignores uncommitted edits). Fix every G1–G8 finding until `gate.py` exits `0` with `"status": "pass"`. Never spend a Boat run on a G1–G8 failure.
   - When removing a declaration, include a `Removed-Declaration: <Qualified::Name>` trailer in the commit message.
   - Add the GPL/EA attribution line from `NOTICE` whenever copying/adapting Zero Hour code or comments.
3. **Mark Ready for review** with evidence in the PR template (RVA, size, offsets/calls, retail vtable slot for virtuals, Zero Hour `path:line` if used, and any differences from Zero Hour — never paste raw disassembly dumps). Automatic Boat admission only triggers on Ready PRs, never Drafts.
4. **Read CI statuses and iterate cleanly (max 3 Boat rounds):**
   - **Do not push** while `BFME II / Admission` says `Held: daily limit`, `checking the source rules`, or `runners unavailable` — those retry automatically.
   - If `BFME II / Landing` reports `Held: merge-forbidden` (HTTP 403), this can happen when `main` updated `.github/workflows/` since your fork branch base; merge/rebase current `main` into your branch and push once.
   - If `BFME II / Boat` refuses the patch, read the structured report (exact byte/relocation diff) and fix the C++ — **max 3 Boat rounds** per function. If still blocked after 3 rounds, record what you learned in `docs/hard_rows.md` or an issue, close the PR, and move to the next target.

## Hard rules summary (G1–G8 & H1–H10)

- **G1 (Paths):** only existing `src/` (never `src/Code/Tools/`) and eligible `include/` paths; no new `.cpp` files; regular UTF-8 files only.
- **G2 (Annotations):** never alter `// FUNCTION:` / `// DATA:` / `// cl*` lines; only `// dxsdk` and `// platformsdk` additions are permitted on existing non-vendored `.c`/`.cpp` files.
- **G3 (Forbidden constructs):** no inline asm, `__emit`, `#pragma optimize`, or banned casts/pragmas.
- **G4 (Stand-in types):** no `void *` or raw integer stand-ins where real engine types exist.
- **G5 (Comments):** no raw hex addresses (`0x...`), dates, or author/model tags in new source comments (put evidence in commit messages and PR descriptions, not source comments!).
- **G6 (Scope):** one logical item per PR (`<= 3` substantive source files, except Draft promotions).
- **G7 (One definition):** never add a duplicate private copy of a class/struct/global already defined in `include/`.
- **G8 (Address names):** no address-derived identifiers (`rva...`, `sub_...`, `g_00...`).
- **H1–H10 (Headers):** wide Boat parity (H1), compiled symbol-to-ledger match (H2), qualified identities (H3), complete declaration comparison (H4), layout & virtual-slot proof (H5), inline-body rules (H6), Draft coverage & promotion hygiene (H7), inlining/macro controls (H8), and wide admission budget (H10).

Full details: [RULES.md](RULES.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/source-quality.md](docs/source-quality.md), [docs/recipes.md](docs/recipes.md), [docs/swarm.md](docs/swarm.md).
