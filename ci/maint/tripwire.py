"""Canary tripwire: verify authentic github-actions statuses on control/canary PRs (#21, #27, #29)."""

PROTECTED_CANARY_PRS = frozenset({3, 4, 21, 27, 29})
TRUSTED_STATUS_CREATOR_LOGIN = "github-actions"
TRUSTED_STATUS_CREATOR_TYPE = "Bot"


class TripwireTripped(RuntimeError):
    pass


def is_trusted_status(status):
    return (
        isinstance(status, dict)
        and status.get("creator_login") == TRUSTED_STATUS_CREATOR_LOGIN
        and status.get("creator_type") == TRUSTED_STATUS_CREATOR_TYPE
    )


def trusted_status_map(pr):
    """Return {context: status} ONLY for authentic github-actions Bot statuses on the PR's current head commit."""
    if not pr.get("head") or pr.get("status_commit_oid") != pr.get("head"):
        return {}
    return {s["context"]: s for s in (pr.get("statuses") or ()) if is_trusted_status(s)}


def check_canaries(pulls, *, pulls_truncated=False):
    """Return a list of violation strings (empty means all canaries are intact and authenticated)."""
    violations = []
    if pulls_truncated:
        violations.append("open pull request snapshot was truncated before full coverage")
    by_num = {int(pr["number"]): pr for pr in pulls}

    for num in (21, 27, 29):
        pr = by_num.get(num)
        if pr is None:
            violations.append(f"canary PR #{num} is missing from open pull requests")
            continue
        if pr.get("status_commit_oid") != pr.get("head"):
            violations.append(f"canary PR #{num} status commit OID does not match headRefOid")
            continue
        sts = trusted_status_map(pr)
        landing = sts.get("BFME II / Landing") or {}
        if landing.get("state") != "failure" or "refused-branch" not in (landing.get("description") or ""):
            violations.append(
                f"canary PR #{num} trusted Landing status drifted: {landing.get('state')} / {landing.get('description')!r}"
            )
        if num == 27:
            boat = sts.get("BFME II / Boat") or {}
            if boat.get("state") != "failure":
                violations.append(
                    f"control PR #27 trusted Boat status drifted from failure: {boat.get('state')} / {boat.get('description')!r}"
                )
    return violations


def enforce(pulls, journal, *, pulls_truncated=False, now=None):
    """Raise TripwireTripped and set persistent STOP kill-switch if any canary drifted or snapshot was truncated."""
    violations = check_canaries(pulls, pulls_truncated=pulls_truncated)
    if violations:
        reason = "Canary tripwire tripped: " + "; ".join(violations)
        journal.write_stop(reason, now=now)
        raise TripwireTripped(reason)
    return True
