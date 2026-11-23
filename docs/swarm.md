# Running Agent Swarms on BFME2-Rebuild

This guide is for running **1 to N autonomous coding agents** safely and productively against `Open-BFME/BFME2-Rebuild`.
Every eligible Ready PR is verified in a sealed, network-isolated Visual C++ 7.1 (`cl.exe 13.10.3077`) compiler sandbox (Boat) and eligible green PRs auto-merge into `main`.

## 1. Before launching a swarm

1. **Assign disjoint slices up front** so agents never collide on the same TU or RVA:
   - Slice `worklist/unwritten_with_zero_hour_counterpart.tsv` (filter `ready_at_snapshot=yes`, `reference_kind=def`, `body_check=none-found`, small `size_bytes`) or `worklist/files.tsv` by **file** (1 file = 1 agent).
   - Always check open PRs before starting:
     ```sh
     gh pr list -R Open-BFME/BFME2-Rebuild --state open --search "<file or RVA>"
     ```
2. **Respect daily admission budgets:**
   - **Narrow (`.cpp`-only, 1–8 files, <= 8 affected TUs):** fastest lane with the largest daily budget. Per-commit daily deduplication means `main` moving does **not** burn extra quota.
   - **Wide (`include/` edits):** lower daily budget (H10). Prefer `.cpp`-only rows first, and only edit `include/` when a declaration is genuinely missing or promoting an `include/Draft/` header.
3. **Never burn Boat runs on local failures:**
   - Every agent MUST commit locally and run `python3 tools/gates/gate.py . <upstream-main-ref> HEAD --json /tmp/gate-<slug>.json` (using a unique `<slug>` per worker!) until it exits `0` with `"status": "pass"` BEFORE pushing or marking a PR Ready.
4. **Never retry indefinitely:**
   - Cap Boat iterations at **3 rounds per function**. If a function doesn't match after 3 informed iterations, record the compiler/ABI finding in an issue or `docs/hard_rows.md`, close the PR, and move on.
5. **Handling `Held: merge-forbidden` (HTTP 403):**
   - If a PR passed Boat (and Header rules if applicable) but `BFME II / Landing` shows `Held: merge-forbidden`, a workflow file may have changed on `main` since the branch was created. Sync the fork branch with upstream `main` (`git merge upstream/main` or GitHub Update Branch) and push once.

---

## 2. Ready-to-use prompts

### Prompt A — Single Autonomous Contributor Agent (copy-paste ready)

```text
You are contributing to https://github.com/Open-BFME/BFME2-Rebuild (a clean-room
1-to-1 C++ rebuild of Battle for Middle-earth II 1.06 compiling under Visual C++ 7.1).

First, read AGENTS.md, docs/agents.md, docs/swarm.md, and CONTRIBUTING.md in full.

Your task: implement and auto-land ONE clean, byte-matching function (or one small
cohesive set of functions in a single TU).

Workflow:
1. Sync your fork with upstream `main` (https://github.com/Open-BFME/BFME2-Rebuild).
2. Pick one unclaimed target from `worklist/unwritten_with_zero_hour_counterpart.tsv`
   (`ready_at_snapshot=yes`, `reference_kind=def`, `body_check=none-found`,
   `size_bytes <= 200`) — pick randomly or from a slice rather than row 1.
   Verify no open PR mentions its RVA or TU (`gh pr list -R Open-BFME/BFME2-Rebuild --state open --search "<RVA>"`)
   and that the owning `.cpp` still has an empty body below its `// FUNCTION:` line.
3. Claim the target BEFORE writing code (open a Draft PR with `Claim: <RVA>` and
   `Claim: <src/path/File.cpp>`, or ensure your swarm planner assigned an exclusive slice).
4. Disassemble the exact bytes in `baseline/game.dat` at `VA = RVA + 0x400000`,
   and read the Zero Hour counterpart under `reference/` if present. Where Zero Hour
   and `baseline/game.dat` disagree, follow `baseline/game.dat`.
5. Write clean, idiomatic, properly typed C++ directly below the owning `// FUNCTION:`
   line using shared headers under `include/`.
   - Never add inline asm, `__emit`, `#pragma optimize`, `void *` stand-ins,
     duplicate class/global definitions, or address/date/author comments in source.
   - Never edit `// FUNCTION:` or `// cl:` lines, `expected/`, `config/`, `tools/`,
     `ci/`, or anything under `src/Code/Tools/`.
   - Only add `// dxsdk` or `// platformsdk` (non-vendored `.c`/`.cpp`, LF-only) if
     an included header genuinely requires DirectX or Platform SDK headers.
   - Only edit an `include/` header if a required member declaration is missing
     (keep edits within H1-H10's supported subset).
   - Add the EA/GPL `NOTICE` line if adapting Zero Hour code or comments.
6. Commit on top of fresh upstream `main` with an imperative subject (<= 72 chars)
   and evidence in the commit body (RVA, size, retail vtable slot for virtuals,
   Zero Hour `path:line`, and any retail differences). Do NOT paste raw disassembly dumps.
7. Run the local source gate on your committed tree BEFORE pushing:
   `python3 tools/gates/gate.py . <upstream-main-ref> HEAD --json /tmp/gate-<slug>.json`
   Fix every G1-G8 finding until it exits 0 with `"status": "pass"`.
8. Push to your fork and mark the PR **Ready for review** against
   `Open-BFME/BFME2-Rebuild:main` with `Claim: <RVA>` and your evidence summary.
   (Keep `Allow edits by maintainers` enabled.)
9. Monitor the PR statuses (`Admission`, `source rules`, `Boat`, `Header rules`, `Landing`):
   - Wait patiently on `Held: daily limit` or `checking the source rules` (auto-retried).
   - If `Landing` says `Held: merge-forbidden` (HTTP 403) on a green PR, merge
     upstream `main` into your branch and push once.
   - If `Boat` refuses the patch, inspect the diff report, refine the C++, re-run
     `gate.py`, and push — at most 3 Boat rounds total.
   - Eligible green PRs auto-merge without human approval. If still blocked after
     3 rounds, record the concrete blocker in an issue or `docs/hard_rows.md`,
     close the PR, and report the outcome.
```

---

### Prompt B — Multi-Agent Swarm Lead (N parallel agents)

```text
You are leading a swarm of N autonomous coding agents contributing to
https://github.com/Open-BFME/BFME2-Rebuild.

Read AGENTS.md, docs/swarm.md, and docs/agents.md first.

Swarm-lead rules:
1. Fetch fresh upstream `main` and list all currently open PRs on
   `Open-BFME/BFME2-Rebuild` (titles + bodies) to build a claimed-RVA and
   claimed-file exclusion set.
2. Partition worklist targets into N **strictly disjoint** TU/RVA assignments:
   - Prefer 80% narrow `.cpp`-only targets (fast auto-land lane, higher daily budget)
     and at most 20% clean header targets (missing declarations / supported virtual
     overrides / Draft promotions) to stay well within wide daily limits (H10).
   - Never assign two agents to the same `.cpp` file or the same `include/` header.
3. Launch each worker in an isolated git branch/worktree with its exact assigned TU,
   RVA(s), a unique gate report path `/tmp/gate-<worker>.json`, and Prompt A's rules.
   Every worker MUST pass `tools/gates/gate.py` before pushing, enable
   `maintainer_can_modify`, and cap Boat retries at 3 rounds.
4. Track every worker PR through to one of three terminal states:
   - **Auto-merged** (record PR # and merged SHA),
   - **Waiting on automatic quota/retry** (do not push churn commits), or
   - **Cleanly closed after <= 3 Boat rounds** with a structured blocker note.
5. Before starting a second wave, re-fetch upstream `main` and re-scan open PRs.
```

---

### Prompt C — Header & Draft Promotion Specialist

```text
You are promoting one `include/Draft/` header (or adding missing verified
declarations to an existing shared header) on https://github.com/Open-BFME/BFME2-Rebuild.

Read AGENTS.md, RULES.md, and docs/swarm.md first.

Rules for H1-H10 auto-landing:
1. Pick one unclaimed entry from `worklist/draft_headers_in_use.tsv` (or a header
   blocking an unwritten `.cpp` function). Confirm no open PR claims it and claim
   it first in a Draft PR.
2. Verify every class size, field offset, and vtable slot against `baseline/game.dat`
   (and Zero Hour `reference/` only where retail agrees).
3. For Draft promotions: create the permanent header under `include/`, update every
   includer `.cpp` to include the permanent header, and delete the `include/Draft/`
   file in the same PR.
4. For existing shared headers:
   - Adding non-virtual method declarations whose compiled symbol matches the ledger
     (H2) or plain non-inline, non-pure virtual overrides of existing base virtual
     slots (without default arguments or private-base visibility errors, H5) can
     auto-pass H1-H10 without human approval.
   - Moving or adding a data member requires a newly matched function in the same PR
     that reads/writes that member (H5 typed-access witness).
   - Cite the retail vtable slot in the commit message / PR description for any
     virtual method declaration.
   - Never remove an existing declaration without a `Removed-Declaration: <Name>`
     commit trailer.
   - Never add forced inlining (`__forceinline` / `WWINLINE`) unless it corresponds
     to an existing Zero Hour inline declaration (H8).
5. Run `python3 tools/gates/gate.py . <upstream-main-ref> HEAD --json /tmp/gate-<slug>.json`
   until it passes, mark the PR Ready with full evidence, and iterate at most 3 rounds.
```
