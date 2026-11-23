"""CLI entrypoint for ci/maint/ autonomous sweeper & proposer."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from actions import restatus, triage  # noqa: E402
from journal import Journal, LockBusy  # noqa: E402
from poll import GhClient, REPO, fetch_snapshot  # noqa: E402
import tripwire  # noqa: E402


def run_tick(client, journal, *, live=False, allow_branch_update=False, grace_seconds=restatus.DEFAULT_GRACE_SECONDS, now=None):
    with journal.acquire_lock():
        if journal.is_stopped():
            return {"status": "stopped", "reason": journal.stop_reason(), "actions": []}
        snapshot = fetch_snapshot(client)
        report = triage.classify_pulls(snapshot, now=now)
        triage.write_report(report, journal.root)
        tripwire.enforce(snapshot["pulls"], journal, pulls_truncated=bool(snapshot.get("pulls_truncated")), now=now)
        planned = restatus.plan_actions(
            snapshot, allow_branch_update=allow_branch_update, grace_seconds=grace_seconds, now=now
        )
        executed = restatus.execute_actions(planned, client, journal, live=live, now=now)
        return {"status": "ok", "main": snapshot["main"], "triage_counts": report["counts"], "actions": executed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="BFME2-Rebuild autonomous maintainer sweeper & proposer (never approves).")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--state-dir", default="/var/lib/bfme2-maint")
    ap.add_argument("--live", action="store_true", help="Enable bounded non-approving recovery writes (default: dry-run).")
    ap.add_argument("--allow-branch-update", action="store_true", help="Allow PUT update-branch on green stale-workflow 403 PRs.")
    ap.add_argument("--grace-seconds", type=int, default=restatus.DEFAULT_GRACE_SECONDS)
    ap.add_argument("--status", action="store_true", help="Print kill-switch and daily usage status and exit.")
    ap.add_argument("--reset-stop", action="store_true", help="Clear persistent STOP kill-switch file and exit.")
    ap.add_argument("--once", action="store_true", help="Run a single sweep tick (default behavior).")
    args = ap.parse_args(argv)

    journal = Journal(args.state_dir)
    if args.reset_stop:
        cleared = journal.clear_stop()
        print(json.dumps({"cleared": cleared}))
        return 0
    if args.status:
        import datetime
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        by_action, total = journal.counts_for_day(day)
        print(json.dumps({"stopped": journal.is_stopped(), "stop_reason": journal.stop_reason(),
                          "day": day, "counts": by_action, "total": total}, indent=2))
        return 0

    client = GhClient(repo=args.repo, live=args.live)
    try:
        out = run_tick(client, journal, live=args.live, allow_branch_update=args.allow_branch_update,
                       grace_seconds=args.grace_seconds)
    except LockBusy as exc:
        print(json.dumps({"status": "lock_busy", "reason": str(exc)}), file=sys.stderr)
        return 3
    except tripwire.TripwireTripped as exc:
        print(json.dumps({"status": "tripwire_tripped", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
