"""Atomic SQLite idempotency journal, advisory process lock, daily caps, kill-switch, and JSONL audit log."""
import contextlib
import datetime
import fcntl
import json
from pathlib import Path
import sqlite3

DEFAULT_DAILY_CAPS = {
    "admit_poke": 20,
    "update_branch_403": 15,
}
DEFAULT_GLOBAL_DAILY_CAP = 30


class LockBusy(RuntimeError):
    pass


class Journal:
    def __init__(self, state_dir, *, daily_caps=None, global_daily_cap=DEFAULT_GLOBAL_DAILY_CAP):
        self.root = Path(state_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "maint.sqlite3"
        self.audit_path = self.root / "audit.jsonl"
        self.stop_path = self.root / "STOP"
        self.lock_path = self.root / "maint.lock"
        self.daily_caps = dict(DEFAULT_DAILY_CAPS if daily_caps is None else daily_caps)
        self.global_daily_cap = int(global_daily_cap)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)

    def _init_db(self):
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "  action TEXT NOT NULL,"
                "  pr INTEGER NOT NULL,"
                "  head TEXT NOT NULL,"
                "  main TEXT NOT NULL,"
                "  outcome TEXT NOT NULL,"
                "  detail TEXT NOT NULL,"
                "  created_day TEXT NOT NULL,"
                "  created_at TEXT NOT NULL,"
                "  PRIMARY KEY (action, pr, head, main)"
                ")"
            )
        finally:
            conn.close()

    @contextlib.contextmanager
    def acquire_lock(self):
        fh = self.lock_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusy("Another ci/maint tick is currently running.") from exc
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    def is_stopped(self):
        return self.stop_path.exists()

    def stop_reason(self):
        if not self.stop_path.exists():
            return None
        return self.stop_path.read_text(encoding="utf-8", errors="replace").strip()

    def write_stop(self, reason, now=None):
        ts = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
        self.stop_path.write_text(f"{ts} {reason}\n", encoding="utf-8")
        self.audit("kill_switch_set", {"reason": reason, "timestamp": ts})

    def clear_stop(self):
        if self.stop_path.exists():
            self.stop_path.unlink()
            self.audit("kill_switch_cleared", {})
            return True
        return False

    def audit(self, event, payload, now=None):
        ts = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
        row = {"ts": ts, "event": event, **payload}
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def counts_for_day(self, day):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT action, COUNT(*) FROM actions WHERE created_day = ? AND outcome != 'error_definite' GROUP BY action",
                (day,),
            ).fetchall()
        finally:
            conn.close()
        by_action = {a: c for a, c in rows}
        return by_action, sum(by_action.values())

    def can_execute(self, action, pr, head, main, *, now=None):
        """Read-only eligibility check (used in dry-run preview; live writes use reserve())."""
        if self.is_stopped():
            return False, "kill-switch active"
        day = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%d")
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT outcome FROM actions WHERE action = ? AND pr = ? AND head = ? AND main = ?",
                (action, int(pr), str(head), str(main)),
            )
            existing = cur.fetchone()
            if existing is not None:
                return False, f"already recorded ({existing[0]})"
            rows = conn.execute(
                "SELECT action, COUNT(*) FROM actions WHERE created_day = ? AND outcome != 'error_definite' GROUP BY action",
                (day,),
            ).fetchall()
        finally:
            conn.close()
        by_action = {a: c for a, c in rows}
        if sum(by_action.values()) >= self.global_daily_cap:
            return False, f"global daily cap ({self.global_daily_cap}) reached"
        cap = self.daily_caps.get(action, 0)
        if by_action.get(action, 0) >= cap:
            return False, f"daily cap for {action} ({cap}) reached"
        return True, "ok"

    def reserve(self, action, pr, head, main, detail="", *, now=None):
        """Atomically check caps + PK and insert a 'reserved' row inside BEGIN IMMEDIATE before any GitHub write."""
        if self.is_stopped():
            return False, "kill-switch active"
        dt = now or datetime.datetime.now(datetime.timezone.utc)
        day = dt.strftime("%Y-%m-%d")
        ts = dt.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT outcome FROM actions WHERE action = ? AND pr = ? AND head = ? AND main = ?",
                (action, int(pr), str(head), str(main)),
            ).fetchone()
            if existing is not None:
                conn.execute("ROLLBACK")
                return False, f"already recorded ({existing[0]})"
            rows = conn.execute(
                "SELECT action, COUNT(*) FROM actions WHERE created_day = ? AND outcome != 'error_definite' GROUP BY action",
                (day,),
            ).fetchall()
            by_action = {a: c for a, c in rows}
            if sum(by_action.values()) >= self.global_daily_cap:
                conn.execute("ROLLBACK")
                return False, f"global daily cap ({self.global_daily_cap}) reached"
            cap = self.daily_caps.get(action, 0)
            if by_action.get(action, 0) >= cap:
                conn.execute("ROLLBACK")
                return False, f"daily cap for {action} ({cap}) reached"
            conn.execute(
                "INSERT INTO actions(action, pr, head, main, outcome, detail, created_day, created_at) "
                "VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)",
                (action, int(pr), str(head), str(main), str(detail), day, ts),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        self.audit("action_reserved", {"action": action, "pr": int(pr), "head": head, "main": main, "detail": detail}, now=dt)
        return True, "reserved"

    def finalize(self, action, pr, head, main, outcome, detail="", *, now=None):
        """Update a reserved row to 'executed', 'error_definite', or 'error_ambiguous'."""
        dt = now or datetime.datetime.now(datetime.timezone.utc)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE actions SET outcome = ?, detail = ? WHERE action = ? AND pr = ? AND head = ? AND main = ?",
                (str(outcome), str(detail), action, int(pr), str(head), str(main)),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        self.audit(
            "action_finalized",
            {"action": action, "pr": int(pr), "head": head, "main": main, "outcome": outcome, "detail": detail},
            now=dt,
        )
