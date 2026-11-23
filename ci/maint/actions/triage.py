"""Read-only PR classifier producing structured triage manifests (zero GitHub writes)."""
import datetime
import json
from pathlib import Path

from tripwire import PROTECTED_CANARY_PRS, trusted_status_map


def classify_pulls(snapshot, *, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    buckets = {
        "ledger_request": [],
        "h5_held": [],
        "boat_failure": [],
        "stale_branch_403": [],
        "quota_held": [],
        "source_rules_failure": [],
        "draft": [],
        "active": [],
    }
    for pr in snapshot.get("pulls") or ():
        num = int(pr["number"])
        if num in PROTECTED_CANARY_PRS:
            continue
        sts = trusted_status_map(pr)
        adm = sts.get("BFME II / Admission") or {}
        src = sts.get("BFME II / source rules") or {}
        boat = sts.get("BFME II / Boat") or {}
        hdr = sts.get("BFME II / Header rules") or {}
        land = sts.get("BFME II / Landing") or {}
        entry = {
            "number": num,
            "author": pr.get("author") or "",
            "title": pr.get("title") or "",
            "head": pr.get("head") or "",
            "base": pr.get("base") or "",
            "maintainer_can_modify": bool(pr.get("maintainer_can_modify")),
            "admission": adm.get("description") or "",
            "boat": boat.get("state") or "",
            "header_rules": hdr.get("description") or "",
            "landing": land.get("description") or "",
        }
        hdr_desc = hdr.get("description") or ""
        adm_desc = adm.get("description") or ""
        land_desc = land.get("description") or ""
        if pr.get("draft"):
            buckets["draft"].append(entry)
        elif "Held: ledger request" in hdr_desc:
            buckets["ledger_request"].append(entry)
        elif hdr_desc.startswith("Held: H5 "):
            buckets["h5_held"].append(entry)
        elif land_desc.startswith("Held: merge-forbidden."):
            buckets["stale_branch_403"].append(entry)
        elif "daily limit" in adm_desc.lower() or "daily limit" in hdr_desc.lower():
            buckets["quota_held"].append(entry)
        elif src.get("state") == "failure":
            buckets["source_rules_failure"].append(entry)
        elif boat.get("state") == "failure":
            buckets["boat_failure"].append(entry)
        else:
            buckets["active"].append(entry)
    return {
        "generated_at": now.isoformat(),
        "main": snapshot["main"],
        "pulls_truncated": bool(snapshot.get("pulls_truncated")),
        "counts": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
    }


def write_report(report, state_dir):
    out = Path(state_dir) / "triage-report.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out
