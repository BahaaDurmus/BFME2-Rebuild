# ci/maint — Trusted Autonomous Maintainer Sweeper & Proposer

`ci/maint/` is a host-side maintenance daemon for `Open-BFME/BFME2-Rebuild` that runs **outside** PR CI (for example via systemd timer on a maintainer host).

## Security boundary (proposer & sweeper ONLY — never an approver)

`ci/maint/` eliminates routine manual babysitting without weakening a single gate:

- **Never approves** pull requests (never in `ci/landing/landing.json` `header_approvers`, never submits PR approvals, never applies `boat-ok`, never writes commit statuses).
- **Never merges** pull requests directly (`landing.yml` remains the sole mover of `main`).
- **Never touches canary/rehearsal PRs** (`#3`, `#4`, `#21`, `#27`, `#29`) or Draft PRs.
- **Authenticates commit statuses:** every status read by `tripwire.py` and `restatus.py` must belong to the PR's current `headRefOid` and be authored by the `github-actions` Bot account (`creator.login == "github-actions"` and `__typename == "Bot"`). Spoofed or stale-commit statuses are ignored (or fail closed in the tripwire).
- **Canary tripwire on every tick (`tripwire.py`):** before executing any GitHub write, verifies that the open-PR snapshot is complete (not truncated) and that `#21` (`Landing: failure`), `#27` (`Boat: failure`, `Landing: failure`), and `#29` (`Landing: failure`) remain intact with authentic `github-actions` statuses. Any drift writes a local `STOP` kill-switch file and halts immediately.
- **Atomic idempotency, locking & caps (`journal.py`):** an advisory `flock` lock prevents overlapping ticks; a `BEGIN IMMEDIATE` SQLite reservation records `(action, pr, head, main)` and enforces per-action and global daily caps **before** any GitHub write is sent, preventing double-sends on crash or concurrent invocations.
- **Zero GitHub writes in dry-run (default):** `cli.py` performs zero GitHub writes unless `--live` is passed explicitly (local files under `--state-dir` — SQLite journal, `triage-report.json`, and `STOP` on tripwire drift — are still updated). Branch updates for stale-workflow HTTP 403 holds additionally require `--allow-branch-update` and should run with a **non-owner** GitHub token so `synchronize` events do not mark `owner_origin`.

## Actions

1. **`restatus.py` (bounded non-approving sweeper):**
   - `admit_poke`: dispatches `bfme-admit-request` when a PR has an authentic `BFME II / source rules` success for current `main` past the grace window AND its `BFME II / Admission` status is in an explicit pokeable state (missing, or pending on an older `main` — never quota-held, maintainer-held, conflict-held, or outage-held).
   - `update_branch_403` *(opt-in via `--allow-branch-update`)*: re-reads live PR head/statuses, verifies via `compare/{base}...{main}` that `.github/workflows/` actually changed between `base` and `main`, confirms non-truncated file coverage and authentic current-`main` `Boat: success` (plus `Header rules: success` if `include/` is touched) with `maintainer_can_modify: true`, and calls `PUT /pulls/{n}/update-branch` with `expected_head_sha`.
2. **`triage.py` (read-only classifier):**
   - Classifies all non-canary open PRs into structured buckets (`ledger_request`, `h5_held`, `boat_failure`, `stale_branch_403`, `quota_held`, `source_rules_failure`, `draft`, `active`) and atomically writes `<state-dir>/triage-report.json`.

## Usage

```sh
# Dry run (default — zero GitHub writes; writes local triage-report.json / journal / STOP)
python3 -B ci/maint/cli.py --state-dir /var/lib/bfme2-maint --once

# Live sweeper (admit pokes only)
python3 -B ci/maint/cli.py --state-dir /var/lib/bfme2-maint --live --once

# Live sweeper + stale-workflow 403 branch updates (use a non-owner bot token)
python3 -B ci/maint/cli.py --state-dir /var/lib/bfme2-maint --live --allow-branch-update --once

# Inspect status / clear STOP kill-switch after investigating a tripwire
python3 -B ci/maint/cli.py --state-dir /var/lib/bfme2-maint --status
python3 -B ci/maint/cli.py --state-dir /var/lib/bfme2-maint --reset-stop
```
