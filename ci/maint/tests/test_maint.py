"""Unit tests for ci/maint/ autonomous sweeper & proposer."""
import datetime
from pathlib import Path
import sys
import tempfile
import unittest

MAINT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAINT_DIR))

from actions import restatus, triage  # noqa: E402
from journal import Journal, LockBusy  # noqa: E402
from poll import GhApiError  # noqa: E402
import tripwire  # noqa: E402

MAIN = "a" * 40
OLD_MAIN = "b" * 40
NOW = datetime.datetime(2026, 11, 22, 23, 0, 0, tzinfo=datetime.timezone.utc)
OLD_TS = "2026-11-22T22:45:00Z"


def bot_status(context, state, description, *, created_at=OLD_TS, login="github-actions", kind="Bot"):
    return {
        "context": context,
        "state": state,
        "description": description,
        "created_at": created_at,
        "creator_login": login,
        "creator_type": kind,
    }


def healthy_canaries():
    return [
        {"number": 21, "title": "Control B068", "draft": True, "maintainer_can_modify": False,
         "base": OLD_MAIN, "head": "1" * 40, "status_commit_oid": "1" * 40, "author": "Ancalgonn",
         "files": ["src/a.cpp"], "files_truncated": False,
         "statuses": [bot_status("BFME II / Landing", "failure", "Held: refused-branch.")]},
        {"number": 27, "title": "Control refuse", "draft": True, "maintainer_can_modify": False,
         "base": OLD_MAIN, "head": "2" * 40, "status_commit_oid": "2" * 40, "author": "Ancalgonn",
         "files": ["src/b.cpp"], "files_truncated": False,
         "statuses": [
             bot_status("BFME II / Boat", "failure", "refused"),
             bot_status("BFME II / Landing", "failure", "Held: refused-branch."),
         ]},
        {"number": 29, "title": "Canary Bridge", "draft": False, "maintainer_can_modify": False,
         "base": OLD_MAIN, "head": "3" * 40, "status_commit_oid": "3" * 40, "author": "Ancalgonn",
         "files": ["include/Bridge.h"], "files_truncated": False,
         "statuses": [bot_status("BFME II / Landing", "failure", "Held: refused-branch.")]},
    ]


class FakeClient:
    def __init__(self, *, live=False, wf_changed=True, fresh_pulls=None, fail_put=None):
        self.live = live
        self.wf_changed = wf_changed
        self.fresh_pulls = fresh_pulls or {}
        self.fail_put = fail_put
        self.writes = []

    def get(self, path):
        if path.startswith("compare/"):
            return {"files": [{"filename": ".github/workflows/landing.yml"}] if self.wf_changed else [{"filename": "src/x.cpp"}]}
        if path.startswith("pulls/"):
            num = int(path.split("/")[1])
            return self.fresh_pulls[num]
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, payload=None):
        if not self.live:
            raise RuntimeError("dry-run")
        self.writes.append(("POST", path, payload))
        return {}

    def put(self, path, payload=None):
        if not self.live:
            raise RuntimeError("dry-run")
        self.writes.append(("PUT", path, payload))
        if self.fail_put is not None:
            raise self.fail_put
        return {}


class MaintTests(unittest.TestCase):
    def test_tripwire_authenticates_bot_creator_commit_oid_and_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            j = Journal(td)
            self.assertTrue(tripwire.enforce(healthy_canaries(), j, now=NOW))

            # Truncated PR list trips immediately
            with self.assertRaises(tripwire.TripwireTripped):
                tripwire.enforce(healthy_canaries(), j, pulls_truncated=True, now=NOW)
            self.assertTrue(j.clear_stop())

            # Spoofed status creator on #27 trips immediately
            spoofed = healthy_canaries()
            spoofed[1]["statuses"][0]["creator_login"] = "random-user"
            spoofed[1]["statuses"][0]["creator_type"] = "User"
            with self.assertRaises(tripwire.TripwireTripped):
                tripwire.enforce(spoofed, j, now=NOW)
            self.assertTrue(j.clear_stop())

            # Stale status commit OID trips immediately
            stale = healthy_canaries()
            stale[0]["status_commit_oid"] = "9" * 40
            with self.assertRaises(tripwire.TripwireTripped):
                tripwire.enforce(stale, j, now=NOW)
            self.assertTrue(j.clear_stop())

    def test_atomic_reservation_lock_and_definite_error_quota(self):
        with tempfile.TemporaryDirectory() as td:
            j = Journal(td, daily_caps={"admit_poke": 2}, global_daily_cap=3)
            with j.acquire_lock():
                with self.assertRaises(LockBusy):
                    with j.acquire_lock():
                        pass

            ok, _ = j.reserve("admit_poke", 43, "h1", MAIN, "t", now=NOW)
            self.assertTrue(ok)
            # Duplicate reservation refused even before finalize
            ok2, why2 = j.reserve("admit_poke", 43, "h1", MAIN, "t", now=NOW)
            self.assertFalse(ok2)
            self.assertIn("reserved", why2)
            j.finalize("admit_poke", 43, "h1", MAIN, "error_definite", "HTTP 422", now=NOW)

            # Definite rejection does not consume daily quota
            self.assertEqual(j.counts_for_day("2026-11-22")[1], 0)

            self.assertTrue(j.reserve("admit_poke", 44, "h2", MAIN, "t", now=NOW)[0])
            j.finalize("admit_poke", 44, "h2", MAIN, "executed", "ok", now=NOW)
            self.assertTrue(j.reserve("admit_poke", 45, "h3", MAIN, "t", now=NOW)[0])
            j.finalize("admit_poke", 45, "h3", MAIN, "executed", "ok", now=NOW)
            ok3, why3 = j.reserve("admit_poke", 46, "h4", MAIN, "t", now=NOW)
            self.assertFalse(ok3)
            self.assertIn("daily cap", why3)

    def test_restatus_allowlist_workflow_diff_and_revalidation(self):
        main_prefix = MAIN[:12]
        pulls = healthy_canaries() + [
            # Eligible admit_poke (missing Admission)
            {"number": 60, "title": "StuckAdmit", "draft": False, "maintainer_can_modify": False,
             "base": MAIN, "head": "6" * 40, "status_commit_oid": "6" * 40, "author": "u1",
             "files": ["src/q.cpp"], "files_truncated": False,
             "statuses": [bot_status("BFME II / source rules", "success", f"Source rules passed (main {main_prefix})")]},
            # NOT eligible admit_poke: maintainer-held / outage / quota-held
            {"number": 61, "title": "MaintHeld", "draft": False, "maintainer_can_modify": False,
             "base": MAIN, "head": "7" * 40, "status_commit_oid": "7" * 40, "author": "u2",
             "files": ["src/r.cpp"], "files_truncated": False,
             "statuses": [
                 bot_status("BFME II / source rules", "success", f"Source rules passed (main {main_prefix})"),
                 bot_status("BFME II / Admission", "pending", "Held: a maintainer will look at this change"),
             ]},
            # Eligible update_branch_403 (current-main trusted Boat + non-truncated files)
            {"number": 58, "title": "Stream", "draft": False, "maintainer_can_modify": True,
             "base": OLD_MAIN, "head": "5" * 40, "status_commit_oid": "5" * 40, "author": "u3",
             "files": ["src/p.cpp"], "files_truncated": False,
             "statuses": [
                 bot_status("BFME II / Boat", "success", f"BFME II comparison passed (main {main_prefix})"),
                 bot_status("BFME II / Landing", "failure", "Held: merge-forbidden. Expected an updated head."),
             ]},
            # NOT eligible update_branch_403: truncated files list
            {"number": 59, "title": "BigPR", "draft": False, "maintainer_can_modify": True,
             "base": OLD_MAIN, "head": "8" * 40, "status_commit_oid": "8" * 40, "author": "u4",
             "files": ["src/p.cpp"], "files_truncated": True,
             "statuses": [
                 bot_status("BFME II / Boat", "success", f"BFME II comparison passed (main {main_prefix})"),
                 bot_status("BFME II / Landing", "failure", "Held: merge-forbidden. Expected an updated head."),
             ]},
        ]
        snap = {"main": MAIN, "pulls": pulls, "pulls_truncated": False}
        planned = restatus.plan_actions(snap, allow_branch_update=True, now=NOW)
        self.assertEqual([(p["action"], p["pr"]) for p in planned], [
            ("admit_poke", 60),
            ("update_branch_403", 58),
        ])

        with tempfile.TemporaryDirectory() as td:
            j = Journal(td)
            # Without .github/workflows/ diff, update_branch_403 is skipped!
            no_wf = FakeClient(live=True, wf_changed=False, fresh_pulls={
                58: {"state": "open", "draft": False, "maintainer_can_modify": True, "head": {"sha": "5" * 40}}
            })
            res = restatus.execute_actions(planned, no_wf, j, live=True, now=NOW)
            self.assertEqual([(r["pr"], r["status"]) for r in res], [(60, "executed"), (58, "skipped")])

            # With .github/workflows/ diff, PR #58 executes once and skips on second tick
            yes_wf = FakeClient(live=True, wf_changed=True, fresh_pulls={
                58: {"state": "open", "draft": False, "maintainer_can_modify": True, "head": {"sha": "5" * 40}}
            })
            res2 = restatus.execute_actions(planned, yes_wf, j, live=True, now=NOW)
            self.assertEqual([(r["pr"], r["status"]) for r in res2], [(60, "skipped"), (58, "executed")])
            res3 = restatus.execute_actions(planned, yes_wf, j, live=True, now=NOW)
            self.assertTrue(all(r["status"] == "skipped" for r in res3))

    def test_triage_classifies_trusted_statuses_and_skips_canaries(self):
        pulls = healthy_canaries() + [
            {"number": 49, "title": "Radar", "draft": False, "maintainer_can_modify": True,
             "base": OLD_MAIN, "head": "7" * 40, "status_commit_oid": "7" * 40, "author": "u1",
             "files": ["include/W3DRadar.h"], "files_truncated": False,
             "statuses": [bot_status("BFME II / Header rules", "pending", "Held: H5 W3DRadar: a slot change needs a witness")]},
            {"number": 15, "title": "Quota", "draft": False, "maintainer_can_modify": False,
             "base": OLD_MAIN, "head": "8" * 40, "status_commit_oid": "8" * 40, "author": "u2",
             "files": ["include/Apt.h"], "files_truncated": False,
             "statuses": [bot_status("BFME II / Admission", "pending", "Held: daily limit, retried automatically")]},
            {"number": 16, "title": "Draft", "draft": True, "maintainer_can_modify": False,
             "base": OLD_MAIN, "head": "9" * 40, "status_commit_oid": "9" * 40, "author": "u2",
             "files": ["src/f.cpp"], "files_truncated": False, "statuses": []},
        ]
        rep = triage.classify_pulls({"main": MAIN, "pulls": pulls, "pulls_truncated": False}, now=NOW)
        self.assertEqual(rep["counts"]["h5_held"], 1)
        self.assertEqual(rep["counts"]["quota_held"], 1)
        self.assertEqual(rep["counts"]["draft"], 1)
        self.assertEqual(sum(rep["counts"].values()), 3)


if __name__ == "__main__":
    unittest.main()
