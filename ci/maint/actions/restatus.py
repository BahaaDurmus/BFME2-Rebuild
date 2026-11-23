"""Bounded, strictly non-approving stuck-PR sweeper actions."""
import datetime

from poll import GhApiError
from tripwire import PROTECTED_CANARY_PRS, trusted_status_map

DEFAULT_GRACE_SECONDS = 300


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(status, now):
    dt = _parse_iso((status or {}).get("created_at"))
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds())


def _is_pokeable_admission(adm, main_prefix):
    """Explicit allowlist of Admission states that legitimately benefit from a single bfme-admit-request poke."""
    if not adm:
        return True
    state = adm.get("state") or ""
    desc = adm.get("description") or ""
    if f"(main {main_prefix})" in desc or f"main {main_prefix}" in desc:
        return False
    # Only poke when Admission is genuinely missing or stuck on a stale-main pending/success state;
    # NEVER poke quota-held, maintainer-held, conflict-held, outage-held, preview-held, or failure states.
    if state == "success" and desc.startswith("Admitted by rule:"):
        return True
    if state == "pending" and desc.startswith("Held: checking the source rules on main "):
        return True
    return False


def _workflows_changed_between(client, base, main):
    """Verify via GitHub compare API that .github/workflows/ actually changed between base and main."""
    if not base or not main or base == main:
        return False
    try:
        cmp_data = client.get(f"compare/{base}...{main}")
    except Exception:
        return False
    for f in cmp_data.get("files") or ():
        fn = f.get("filename") or ""
        if fn.startswith(".github/workflows/"):
            return True
    return False


def plan_actions(snapshot, *, allow_branch_update=False, grace_seconds=DEFAULT_GRACE_SECONDS, now=None):
    """Return a list of planned recovery actions (pure function — zero side effects)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    main = snapshot["main"]
    main_prefix = main[:12]
    planned = []

    for pr in snapshot.get("pulls") or ():
        num = int(pr["number"])
        if num in PROTECTED_CANARY_PRS or pr.get("draft"):
            continue
        head = pr.get("head") or ""
        if not head or pr.get("status_commit_oid") != head:
            continue
        sts = trusted_status_map(pr)
        adm = sts.get("BFME II / Admission") or {}
        src = sts.get("BFME II / source rules") or {}
        boat = sts.get("BFME II / Boat") or {}
        hdr = sts.get("BFME II / Header rules") or {}
        land = sts.get("BFME II / Landing") or {}

        # 1. Source rules passed on current main, but Admission is missing or stuck on an older main after grace
        src_desc = src.get("description") or ""
        if src.get("state") == "success" and f"(main {main_prefix})" in src_desc:
            if _is_pokeable_admission(adm, main_prefix):
                age = _age_seconds(src, now)
                if age is not None and age >= grace_seconds:
                    planned.append({
                        "action": "admit_poke",
                        "pr": num,
                        "head": head,
                        "main": main,
                        "reason": f"PR #{num} passed source rules on {main_prefix} with missing/stale Admission",
                    })
                    continue

        # 2. Stale-workflow HTTP 403 merge-forbidden on an otherwise green PR with complete file list & maintainer_can_modify
        if allow_branch_update and pr.get("maintainer_can_modify") and not pr.get("files_truncated"):
            touches_include = any(p.startswith("include/") for p in pr.get("files") or ())
            land_desc = land.get("description") or ""
            boat_desc = boat.get("description") or ""
            hdr_desc = hdr.get("description") or ""
            boat_ok = boat.get("state") == "success" and f"(main {main_prefix})" in boat_desc
            hdr_ok = (not touches_include) or (hdr.get("state") == "success" and f"(main {main_prefix})" in hdr_desc)
            if (land.get("state") == "failure"
                    and land_desc.startswith("Held: merge-forbidden.")
                    and boat_ok and hdr_ok and pr.get("base") and pr.get("base") != main):
                planned.append({
                    "action": "update_branch_403",
                    "pr": num,
                    "head": head,
                    "base": pr.get("base"),
                    "main": main,
                    "reason": f"PR #{num} passed checks on {main_prefix} and hit merge-forbidden (base {pr.get('base','')[:10]} != {main_prefix})",
                })

    return planned


def execute_actions(actions, client, journal, *, live=False, now=None):
    """Execute planned actions using atomic SQLite reservation before any GitHub write."""
    results = []
    for item in actions:
        act, num, head, main = item["action"], item["pr"], item["head"], item["main"]
        if not live:
            ok, why = journal.can_execute(act, num, head, main, now=now)
            status = "dry-run" if ok else "skipped"
            results.append({**item, "status": status, "detail": item["reason"] if ok else why})
            continue

        # Pre-flight verification for update_branch_403 BEFORE reserving/mutating
        if act == "update_branch_403":
            if not _workflows_changed_between(client, item.get("base", ""), main):
                results.append({**item, "status": "skipped", "detail": "no .github/workflows/ diff between base and main"})
                continue
            try:
                fresh = client.get(f"pulls/{num}")
            except Exception as exc:
                results.append({**item, "status": "skipped", "detail": f"failed to revalidate PR head: {str(exc)[:120]}"})
                continue
            if (fresh.get("state") != "open" or fresh.get("draft")
                    or not fresh.get("maintainer_can_modify")
                    or (fresh.get("head") or {}).get("sha") != head):
                results.append({**item, "status": "skipped", "detail": "PR head or eligibility changed before write"})
                continue

        ok, why = journal.reserve(act, num, head, main, item["reason"], now=now)
        if not ok:
            results.append({**item, "status": "skipped", "detail": why})
            continue
        try:
            if act == "admit_poke":
                client.post("dispatches", {"event_type": "bfme-admit-request", "client_payload": {"number": num}})
            elif act == "update_branch_403":
                client.put(f"pulls/{num}/update-branch", {"expected_head_sha": head})
            else:
                raise ValueError(f"Unknown action: {act}")
            journal.finalize(act, num, head, main, "executed", item["reason"], now=now)
            results.append({**item, "status": "executed", "detail": item["reason"]})
        except GhApiError as exc:
            outcome = "error_definite" if exc.definite else "error_ambiguous"
            journal.finalize(act, num, head, main, outcome, str(exc)[:200], now=now)
            results.append({**item, "status": outcome, "detail": str(exc)[:200]})
        except Exception as exc:
            journal.finalize(act, num, head, main, "error_ambiguous", str(exc)[:200], now=now)
            results.append({**item, "status": "error_ambiguous", "detail": str(exc)[:200]})
    return results
