from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import forms as F
from . import labmodel
from .catalog import BY_SLUG, CATALOG
from .models import WorkflowRequest


def is_operator(user):
    return user.is_superuser or user.has_perm("workflows.view_all_requests")


def lab_context(request):
    return {"is_operator": request.user.is_authenticated and is_operator(request.user)}


def visible(user):
    qs = WorkflowRequest.objects.select_related("requested_by")
    return qs if is_operator(user) else qs.filter(requested_by=user)


@login_required
def dashboard(request):
    mine = WorkflowRequest.objects.filter(requested_by=request.user)[:20]
    counts = {s: visible(request.user).filter(status=s).count() for s in ("submitted", "approved", "running")}
    try: lab = labmodel.model(); lab_error = None
    except Exception as e: lab, lab_error = None, f"{type(e).__name__}: {e}"   # noqa: BLE001
    return render(request, "workflows/dashboard.html", {"mine": mine, "catalog": CATALOG, "counts": counts, "lab": lab, "lab_error": lab_error})


@login_required
def new_request(request, slug):
    wf = BY_SLUG.get(slug)
    if not wf: raise Http404("no such workflow")
    Form = getattr(F, wf.form)
    form = Form(request.POST or None)
    if request.method == "POST" and form.is_valid():
        p = form.params()
        r = WorkflowRequest.objects.create(workflow=wf.slug, title=wf.title.format(**p), parameters=p, reason=form.cleaned_data["reason"],
                                           requested_by=request.user)
        r.log(request.user, "submitted", form.cleaned_data["reason"])
        messages.success(request, f"Request #{r.pk} submitted: {r.title}")
        return redirect(r)
    return render(request, "workflows/new.html", {"wf": wf, "form": form})


@login_required
def request_detail(request, pk):
    r = get_object_or_404(visible(request.user), pk=pk)
    return render(request, "workflows/detail.html", {"r": r, "wf": BY_SLUG.get(r.workflow), "events": r.events.select_related("actor"),
                                                     "can_cancel": r.status in WorkflowRequest.OPEN and (r.requested_by == request.user or is_operator(request.user))})


@login_required
@require_POST
def cancel_request(request, pk):
    r = get_object_or_404(visible(request.user), pk=pk)
    if r.status not in WorkflowRequest.OPEN: messages.error(request, f"#{r.pk} is {r.get_status_display().lower()}: it can no longer be cancelled.")
    elif r.requested_by != request.user and not is_operator(request.user): raise PermissionDenied
    else:
        r.status = WorkflowRequest.Status.CANCELLED; r.save(update_fields=["status", "updated"])
        r.log(request.user, "cancelled", request.POST.get("note", "")); messages.info(request, f"Request #{r.pk} cancelled.")
    return redirect(r)


@login_required
def all_requests(request):
    if not is_operator(request.user): raise PermissionDenied
    status = request.GET.get("status")
    qs = visible(request.user)
    if status: qs = qs.filter(status=status)
    return render(request, "workflows/all.html", {"requests": qs[:200], "status": status, "statuses": WorkflowRequest.Status.choices})


def catalog(request):
    return render(request, "workflows/catalog.html", {"catalog": CATALOG})
