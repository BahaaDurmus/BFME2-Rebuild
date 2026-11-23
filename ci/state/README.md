# Main state on the `state` branch

`ci/managed-state/` (workflow `.github/workflows/state.yml`, "Measure protected main state")
measures every source file of each main commit, re-measuring only the files a commit can
affect and carrying the rest from the previous commit. This directory publishes those
measurements and checks them.

| File | Used by | Does |
| --- | --- | --- |
| `resolve.py` | `state-publish.yml` | chooses the measurement run to publish, or says why there is none |
| `publish.py` | `state-publish.yml` | checks the measurement against the commit's list of source files and writes one commit to `state` |
| `layout.py` | both | the per-file format (see `STATE-BRANCH.md`), split and join |
| `store.py` | both | plain Git access to the `state` branch |
| `parity.py` | `state-parity.yml` | weekly: a full recompute must equal the published state byte for byte |
| `tests/` | anyone | offline tests: `python3 -B -m unittest discover -s ci/state/tests -v` |

Switch: repository variable `BFME_STATE_BRANCH_ENABLED=yes` (publication); the weekly check also
needs `BFME_STATE_WORKFLOW_ENABLED=yes` and `BFME_STATE_BUDGET_JOBS` (the measurement's own switches).

After a landing pushed with the Actions token (which starts no workflow), start the measurement
with `workflow_dispatch` of `state.yml`; publication follows by itself (`workflow_run`, with a
30-minute schedule as a fallback).

Failures open one issue each through `ci/alarms/alarm.py`: `state-measure`, `state-publish`,
`state-stale`, `state-parity`. Each closes by itself when the same job next succeeds.
To test the alarm, run "Publish main state" by hand with `drill` ticked: the run must fail and the
issue "Alarm: Main state could not be published" must open; the next ordinary run closes it.

## Recovery & alarms

- Alarm issues are filed with the **`alarm`** label (not `ci-alarm`) and close automatically on the
  next successful run of the corresponding workflow.
- If a maintainer push changes global configuration or headers without a pinned incremental transition
  in `ci/managed-state/state_delta.py` and hits `BFME_STATE_BUDGET_JOBS`, either add the pinned
  transition (preferred, keeps the refresh incremental) or run `state.yml` via `workflow_dispatch`
  with `seed=true` (temporarily raising `BFME_STATE_BUDGET_JOBS` if a full refresh is intentional).
