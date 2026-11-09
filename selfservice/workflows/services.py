"""What a user may do with a request, and doing it — shared by the classic pages (views.py) and the API (api.py)."""
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import WorkflowRequest as R


def is_operator(user):
    return user.is_superuser or user.has_perm("workflows.view_all_requests")


def can_approve(user):
    return user.is_superuser or user.has_perm("workflows.approve_requests")


def visible(user):
    qs = R.objects.select_related("requested_by", "decided_by")
    return qs if is_operator(user) else qs.filter(requested_by=user)


class Refused(Exception):
    """The request is not in a state that allows it (the message says why)."""


DECISIONS = {"approve": (R.Status.SUBMITTED, R.Status.APPROVED, "approved"),
             "reject": (R.Status.SUBMITTED, R.Status.REJECTED, "rejected"),
             "retry": (R.Status.FAILED, R.Status.APPROVED, "retry")}


def allowed(r, user):
    """The actions this user can take on this request now."""
    op = can_approve(user)
    return {"approve": op and r.status == R.Status.SUBMITTED, "reject": op and r.status == R.Status.SUBMITTED,
            "retry": op and r.status == R.Status.FAILED,
            "cancel": r.status in R.OPEN and (r.requested_by_id == user.pk or is_operator(user))}


def decide(r, user, decision, note=""):
    if decision not in DECISIONS: raise ValueError(decision)
    if not can_approve(user): raise PermissionDenied
    want, new, kind = DECISIONS[decision]
    if r.status != want: raise Refused(f"#{r.pk} is {r.get_status_display().lower()}: it cannot be {decision}d now.")
    r.status, r.decided_by, r.decided_at = new, user, timezone.now()
    r.save(update_fields=["status", "decided_by", "decided_at", "updated"])
    r.log(user, kind, note.strip())
    return r


def cancel(r, user, note=""):
    if r.status not in R.OPEN: raise Refused(f"#{r.pk} is {r.get_status_display().lower()}: it can no longer be cancelled.")
    if r.requested_by_id != user.pk and not is_operator(user): raise PermissionDenied
    r.status = R.Status.CANCELLED; r.save(update_fields=["status", "updated"])
    r.log(user, "cancelled", note.strip())
    return r


def percent(r):
    """How far along a request is, 0-100: done stages, plus the running stage's share (a pipeline: its finished steps)."""
    if r.status == R.Status.DONE: return 100
    p = r.progress or {}; stages = p.get("stages") or []
    if not stages or r.status in (R.Status.SUBMITTED, R.Status.APPROVED, R.Status.REJECTED, R.Status.CANCELLED): return 0
    done = sum(s["status"] == "done" for s in stages); frac = 0.0
    cur = next((s for s in stages if s["status"] == "running"), None)
    if cur:
        snap = next((v for v in reversed(list(p.get("pipelines", {}).values()))
                     if v["repo"].removeprefix("evpn-3tier-") == cur["key"] and v.get("attempt") == p.get("attempt")), None)
        if snap and snap.get("steps"):
            frac = sum(s["status"] in ("success", "failure", "skipped", "cancelled") for s in snap["steps"]) / len(snap["steps"])
        else:
            frac = 0.3
    return round(100 * (done + frac) / len(stages))
