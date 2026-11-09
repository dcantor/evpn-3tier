"""The JSON API the front end (selfservice/frontend, React) uses — /api/, OpenAPI docs at /api/docs.

Session authentication (Django's login, cookie) with CSRF on every change: the front end is served from the same
origin. Live updates: /api/stream (server-sent events, stream.py) says which requests changed; the front end fetches
them again from here."""
import threading
import time

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from . import forms as F
from . import labmodel, services
from .catalog import BY_SLUG, CATALOG
from .models import WorkflowRequest as R

api = NinjaAPI(title="Fabric Factory", version="1", auth=django_auth, urls_namespace="api",
               description="Fabric Factory: request workflows on the fabric (operators approve; a worker carries them out), and read device configurations and state.")


# ---- serialisation ---------------------------------------------------------------------------------------------------------
def _user(u):
    return u and {"username": u.username, "name": u.get_full_name() or u.username}


def summary(r):
    p = r.progress or {}; stages = p.get("stages") or []
    cur = next((s for s in stages if s["status"] == "running"), None)
    return {"id": r.pk, "title": r.title, "workflow": r.workflow, "workflow_name": BY_SLUG[r.workflow].name if r.workflow in BY_SLUG else r.workflow,
            "status": r.status, "status_label": r.get_status_display(), "requested_by": _user(r.requested_by),
            "decided_by": _user(r.decided_by), "created": r.created, "updated": r.updated, "decided_at": r.decided_at,
            "percent": services.percent(r), "stage": cur and cur["label"], "seq": p.get("seq", 0),
            "error": (r.result or {}).get("error")}


def detail(r, user):
    wf = BY_SLUG.get(r.workflow)
    return {**summary(r), "parameters": r.parameters, "reason": r.reason, "result": r.result, "progress": r.progress,
            "will_do": wf.will_do if wf else [], "stages": [{"key": k, "label": l} for k, l in (wf.stages if wf else ())],
            "events": [{"id": e.pk, "at": e.at, "kind": e.kind, "actor": _user(e.actor), "message": e.message}
                       for e in r.events.select_related("actor")],
            "can": services.allowed(r, user)}


def form_schema(form):
    """A Django form as the front end renders it: fields in order, types, limits, choices, help — and the form's `ui`
    hints (a field allocated by a button, a field filled by the allocation) and `computed` read-only fields."""
    out = []; ui = getattr(form, "ui", {})
    for name, f in form.fields.items():
        w = type(f.widget).__name__
        kind = {"IntegerField": "number", "BooleanField": "checkbox", "ChoiceField": "select", "MultipleChoiceField": "multiselect"}.get(type(f).__name__, "textarea" if w == "Textarea" else "text")
        out.append({"name": name, "label": str(f.label or name.replace("_", " ").capitalize()), "type": kind, "required": f.required,
                    "help": str(f.help_text or ""), "initial": form.get_initial_for_field(f, name),
                    "choices": [{"value": v, "label": str(l)} for v, l in getattr(f, "choices", [])] if kind in ("select", "multiselect") else None,
                    "min": getattr(f, "min_value", None), "max": getattr(f, "max_value", None), "max_length": getattr(f, "max_length", None),
                    **({"type": ui[name]["type"], "ui": ui[name]} if name in ui else {})})
    for c in getattr(form, "computed", []):
        at = next((i for i, x in enumerate(out) if x["name"] == c["after"]), len(out) - 1) + 1
        out.insert(at, {"name": c["name"], "label": c["label"], "type": "computed", "required": False, "help": c["help"], "initial": None,
                        "choices": None, "min": None, "max": None, "max_length": None, "ui": {"template": c["template"]}})
    return out


# ---- session ---------------------------------------------------------------------------------------------------------------
class Login(Schema):
    username: str
    password: str


def me(request):
    u = request.user
    if not u.is_authenticated: return {"authenticated": False, "csrf": get_token(request)}
    return {"authenticated": True, "csrf": get_token(request), "user": _user(u), "is_operator": services.is_operator(u),
            "can_approve": services.can_approve(u), "is_staff": u.is_staff}


@api.get("/session", auth=None, tags=["session"])
def session(request):
    """Who is signed in (and the CSRF token for changes)."""
    return me(request)


@api.post("/session", auth=None, tags=["session"])
def sign_in(request, body: Login):
    from django.middleware.csrf import CsrfViewMiddleware
    if (bad := CsrfViewMiddleware(lambda r: None).process_view(request, None, (), {})) is not None:
        raise HttpError(403, "CSRF check failed: reload the page")
    u = authenticate(request, username=body.username, password=body.password)
    if u is None or not u.is_active: raise HttpError(401, "Wrong user name or password.")
    login(request, u)
    return me(request)


@api.delete("/session", tags=["session"])
def sign_out(request):
    logout(request); return {"authenticated": False, "csrf": get_token(request)}


# ---- the lab and the catalog -----------------------------------------------------------------------------------------------
@api.get("/lab", tags=["lab"])
def lab(request):
    """What the request forms validate against: from Nautobot (lab.conf when Nautobot does not answer)."""
    try: m = labmodel.model()
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(503, f"lab model unavailable: {type(e).__name__}: {e}")
    return {**m, "subnets_used": [str(s) for s in m["subnets_used"]]}


@api.get("/catalog", tags=["catalog"])
def catalog(request):
    return [{"slug": w.slug, "name": w.name, "summary": w.summary, "will_do": w.will_do,
             "stages": [{"key": k, "label": l} for k, l in w.stages]} for w in CATALOG]


@api.get("/catalog/{slug}/form", tags=["catalog"])
def catalog_form(request, slug: str):
    wf = BY_SLUG.get(slug) or _404("no such workflow")
    try: form = getattr(F, wf.form)()
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(503, f"the form needs the lab model: {type(e).__name__}: {e}")
    return {"slug": wf.slug, "name": wf.name, "summary": wf.summary, "will_do": wf.will_do, "fields": form_schema(form)}


@api.get("/catalog/add-vlan/allocate", tags=["catalog"])
def allocate_vlan(request, vrf: str = ""):
    """The next unused VLAN ID and a free /24 from phpIPAM (gateway: its first host) — nothing is reserved yet."""
    from . import allocate
    try: return allocate.add_vlan(vrf or None)
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(503, f"allocation failed: {type(e).__name__}: {e}")


# ---- requests --------------------------------------------------------------------------------------------------------------
class NewRequest(Schema):
    workflow: str
    data: dict


class Note(Schema):
    note: str = ""


def _404(msg):
    raise HttpError(404, msg)


@api.get("/requests", tags=["requests"])
def list_requests(request, scope: str = "mine", status: str = "", q: str = "", limit: int = 100):
    """scope: mine (yours), all (operators), pending (submitted, awaiting a decision — approvers)."""
    u = request.user
    if scope == "mine": qs = R.objects.select_related("requested_by", "decided_by").filter(requested_by=u)
    elif scope == "all":
        if not services.is_operator(u): raise HttpError(403, "operators only")
        qs = services.visible(u)
    elif scope == "pending":
        if not services.can_approve(u): raise HttpError(403, "approvers only")
        qs = services.visible(u).filter(status=R.Status.SUBMITTED).order_by("created")
    else: raise HttpError(400, "scope: mine, all or pending")
    if status: qs = qs.filter(status__in=status.split(","))
    if q: qs = qs.filter(title__icontains=q)
    return [summary(r) for r in qs[:max(1, min(limit, 500))]]


@api.get("/requests/counts", tags=["requests"])
def counts(request):
    qs = services.visible(request.user)
    return {s: qs.filter(status=s).count() for s, _ in R.Status.choices}


@api.post("/requests", tags=["requests"])
def create(request, body: NewRequest):
    wf = BY_SLUG.get(body.workflow) or _404("no such workflow")
    # as a browser form would post it: a ticked checkbox is "on", an unticked one is absent
    data = {k: ("on" if v is True else v) for k, v in body.data.items() if v is not False and v is not None}
    form = getattr(F, wf.form)(data)
    if not form.is_valid():
        return api.create_response(request, {"errors": {k: [str(m) for m in v] for k, v in form.errors.items()}}, status=400)
    p = form.params()
    r = R.objects.create(workflow=wf.slug, title=wf.title.format(**p), parameters=p, reason=form.cleaned_data["reason"], requested_by=request.user)
    r.log(request.user, "submitted", form.cleaned_data["reason"])
    return detail(r, request.user)


@api.get("/requests/{rid}", tags=["requests"])
def get_request(request, rid: int):
    return detail(get_object_or_404(services.visible(request.user), pk=rid), request.user)


@api.post("/requests/{rid}/{action}", tags=["requests"])
def act(request, rid: int, action: str, body: Note):
    """approve / reject (submitted), retry (failed) — approvers; cancel (submitted or approved) — the requester or an operator."""
    r = get_object_or_404(services.visible(request.user) if action == "cancel" else R.objects.all(), pk=rid)
    try:
        if action == "cancel": services.cancel(r, request.user, body.note)
        elif action in services.DECISIONS: services.decide(r, request.user, action, body.note)
        else: raise HttpError(404, "no such action")
    except PermissionDenied: raise HttpError(403, "not allowed")
    except services.Refused as e: raise HttpError(409, str(e))
    return detail(r, request.user)


# ---- a pipeline run's whole log (one step), read live from Gitea -----------------------------------------------------------
_gitea = {"web": None}
_lock = threading.Lock()


@api.get("/requests/{rid}/pipelines/{repo}/{run}/log", tags=["requests"])
def pipeline_log(request, rid: int, repo: str, run: int, step: int):
    r = get_object_or_404(services.visible(request.user), pk=rid)
    if not any(v["repo"] == repo and v.get("run") == run for v in (r.progress or {}).get("pipelines", {}).values()):
        raise HttpError(404, "not a pipeline run of this request")
    from .clients import Gitea, GiteaWeb
    with _lock:
        for attempt in (1, 2):
            try:
                _gitea["web"] = _gitea["web"] or GiteaWeb(Gitea().auth)
                state, lines = _gitea["web"].full_log(repo, run, step)
                break
            except Exception as e:                                            # noqa: BLE001 — a stale session: once more
                _gitea["web"] = None
                if attempt == 2: raise HttpError(502, f"Gitea: {type(e).__name__}: {e}")
    steps = state["currentJob"]["steps"]
    return {"step": step, "name": steps[step]["summary"] if step < len(steps) else "", "status": steps[step]["status"] if step < len(steps) else "",
            "lines": lines}


# ---- devices: configurations and state (read-only, no approval) ---------------------------------------------------------------
import logging                                                                # noqa: E402

from django.http import HttpResponse                                          # noqa: E402

audit = logging.getLogger("workflows.devices")


def _dev(name):
    from . import devices
    try: return devices.device(name)
    except KeyError: raise HttpError(404, f"no device {name}")


@api.get("/devices", tags=["devices"])
def list_devices(request):
    from . import devices
    return {"devices": devices.devices(), "formats": [{"key": k, "label": v[0]} for k, v in devices.VYOS_FORMATS.items()],
            "state": devices.state_catalog()}


@api.get("/devices/{name}/config", tags=["devices"])
def device_config(request, name: str, format: str = "commands"):
    """A device's configuration (VyOS: set commands or hierarchical; Linux: its configuration files). Secrets redacted."""
    from . import devices
    _dev(name); audit.info("%s: configuration of %s (%s)", request.user, name, format)
    try: return devices.config(name, format)
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"{name}: {type(e).__name__}: {e}")


@api.get("/devices-configs.zip", tags=["devices"])
def configs_zip(request, names: str = "", format: str = "commands"):
    """Several devices' configurations as a zip of text files (names: comma-separated; empty: every device)."""
    import io
    import zipfile
    from . import devices
    want = [n for n in names.split(",") if n] or [d["name"] for d in devices.devices()]
    for n in want: _dev(n)
    audit.info("%s: configurations of %s (%s), zip", request.user, ",".join(want), format)
    buf = io.BytesIO(); stamp = time.strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for c in devices.configs(want, format):
            if "error" in c: z.writestr(f"{c['device']}.ERROR.txt", c["error"] + "\n"); continue
            z.writestr(f"{c['device']}.txt" if c["kind"] == "vyos" else f"{c['device']}.conf.txt", c["text"] + "\n")
    r = HttpResponse(buf.getvalue(), content_type="application/zip")
    r["Content-Disposition"] = f'attachment; filename="fabric-factory-configs-{stamp}.zip"'
    return r


@api.get("/devices/{name}/state", tags=["devices"])
def device_state(request, name: str, cats: str = "interfaces"):
    """Live state of a device — categories from GET /devices (interfaces, routes, bgp, evpn, mac, arp, bfd, vrrp, …)."""
    from . import devices
    _dev(name); audit.info("%s: state of %s (%s)", request.user, name, cats)
    try: return devices.state(name, [c for c in cats.split(",") if c])
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"{name}: {type(e).__name__}: {e}")


# ---- fabric dashboard (read-only, no approval) -------------------------------------------------------------------------------
@api.get("/fabric", tags=["fabric"])
def fabric_design(request, refresh: bool = False):
    """The fabric at a glance, from Nautobot: devices by role, models, links, VRFs, VLANs, subnets, design KPIs, change KPIs."""
    from . import fabric
    try: return fabric.design(refresh)
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"Nautobot: {type(e).__name__}: {e}")


@api.get("/fabric/health", tags=["fabric"])
def fabric_health(request, refresh: bool = False):
    """Live health against the design: BGP / BFD sessions, VNIs, software versions, uptime (read-only SSH, cached 30 s)."""
    from . import fabric
    audit.info("%s: fabric health%s", request.user, " (refresh)" if refresh else "")
    try: return fabric.health(refresh)
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"{type(e).__name__}: {e}")


@api.get("/fabric/reconcile", tags=["fabric"])
def fabric_reconcile(request, refresh: bool = False):
    """phpIPAM against Nautobot: VRFs, VLANs, subnets, probe subnets, gateways, assigned addresses, and Nautobot's plan
    against its own objects. Read-only (it repairs nothing); cached 30 s."""
    from . import reconcile
    try: return reconcile.report(refresh)
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"{type(e).__name__}: {e}")


# ---- databases: ASNs and VLANs (read-only, no approval), as JSON and as Excel ---------------------------------------------------
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(data, name):
    r = HttpResponse(data, content_type=XLSX)
    r["Content-Disposition"] = f'attachment; filename="evpn-3tier-{name}-{time.strftime("%Y%m%d-%H%M%S")}.xlsx"'
    return r


@api.get("/tools/asns", tags=["tools"])
def asn_database(request):
    """Every ASN of the fabric with its devices, roles, router IDs and peers, and every BGP session of the design (Nautobot)."""
    from . import dbtools
    try: return dbtools.asn_db()
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"Nautobot: {type(e).__name__}: {e}")


@api.get("/tools/asns.xlsx", tags=["tools"])
def asn_database_xlsx(request):
    """The ASN database as an Excel workbook: ASNs, BGP sessions, About."""
    from . import dbtools
    audit.info("%s: ASN database, xlsx", request.user)
    return _xlsx(dbtools.asn_xlsx(), "asns")


@api.get("/tools/vlans", tags=["tools"])
def vlan_database(request):
    """Every VLAN of the evpn-3tier VLAN group: tier, firewall transit, load balancer peering, L3VNI (Nautobot)."""
    from . import dbtools
    try: return dbtools.vlan_db()
    except Exception as e:                                                    # noqa: BLE001
        raise HttpError(502, f"Nautobot: {type(e).__name__}: {e}")


@api.get("/tools/vlans.xlsx", tags=["tools"])
def vlan_database_xlsx(request):
    """The VLAN database as an Excel workbook: VLANs, About."""
    from . import dbtools
    audit.info("%s: VLAN database, xlsx", request.user)
    return _xlsx(dbtools.vlan_xlsx(), "vlans")
