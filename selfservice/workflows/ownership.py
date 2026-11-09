"""Applications, owners and port profiles — what Port Provisioning records about a port besides its configuration.

In Nautobot (shared with other labs, so everything is prefixed e3t or grouped under evpn-3tier):
  applications   tenants in tenant group e3t-applications; custom field e3t_app_id (APP-<digits>)
  owner          a team, linked to the application by a contact association with role Owner
  port -> app    relationship e3t_port_application (one application, many ports)
  port profile   custom field e3t_port_profile on the interface (the profile's name)
  profiles       the evpn-3tier config context, list port_profiles:
                 {name, description, mode: access|tagged, access_vlan, trunk_vlans, native_vlan}
Ownership is recorded (shown on the port, in its switch description, in the tools), not enforced."""
import re
import time

from .clients import LOCATION, Nautobot

GROUP = "e3t-applications"
CF_APP, CF_PROFILE, REL = "e3t_app_id", "e3t_port_profile", "e3t_port_application"
APP_ID = re.compile(r"^APP-\d{3,6}$")
OWNER_ROLE = "Owner"
_cache = {"t": 0, "v": None}


def ensure_schema(log=print):
    """Create what this needs in Nautobot (idempotent)."""
    nb = Nautobot()
    parent = nb.one("tenancy/tenant-groups", name=LOCATION)
    if not nb.one("tenancy/tenant-groups", name=GROUP):
        nb.post("tenancy/tenant-groups", {"name": GROUP, "parent": parent["id"] if parent else None,
                                          "description": "evpn-3tier applications: owners of switch ports (Fabric Factory)"}); log(f"tenant group {GROUP}")
    cfs = {c["key"]: c for c in nb.get("extras/custom-fields", limit=500)["results"]}
    for key, label, ct, extra in [(CF_APP, "Application ID (evpn-3tier)", "tenancy.tenant", {"validation_regex": APP_ID.pattern}),
                                  (CF_PROFILE, "Port profile (evpn-3tier)", "dcim.interface", {})]:
        if key not in cfs:
            nb.post("extras/custom-fields", {"key": key, "label": label, "type": "text", "content_types": [ct], **extra,
                                             "description": "Fabric Factory (evpn-3tier) Port Provisioning"}); log(f"custom field {key}")
    if not nb.get("extras/relationships", key=REL)["results"]:
        nb.post("extras/relationships", {"key": REL, "label": "Application (evpn-3tier)", "type": "one-to-many",
                                         "source_type": "tenancy.tenant", "source_label": "Switch ports",
                                         "destination_type": "dcim.interface", "destination_label": "Application",
                                         "description": "Fabric Factory (evpn-3tier): the application that owns a switch port"}); log(f"relationship {REL}")
    role = nb.one("extras/roles", name=OWNER_ROLE)
    if role is None:
        nb.post("extras/roles", {"name": OWNER_ROLE, "content_types": ["extras.contactassociation"], "color": "4f46e5"}); log(f"role {OWNER_ROLE}")
    elif "extras.contactassociation" not in role["content_types"]:
        nb.patch(f"extras/roles/{role['id']}", {"content_types": role["content_types"] + ["extras.contactassociation"]}); log(f"role {OWNER_ROLE}: contact associations")
    ctx = nb.context()
    if "port_profiles" not in ctx["data"]:
        nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": {**ctx["data"], "port_profiles": []}}); log("config context: port_profiles")


# ---- applications -------------------------------------------------------------------------------------------------------------
def applications(force=False):
    """[{id, app_id, name, description, owner: {id, name} | None, label}] — cached 30 s."""
    if _cache["v"] is not None and not force and time.time() - _cache["t"] < 30: return _cache["v"]
    nb = Nautobot()
    g = nb.one("tenancy/tenant-groups", name=GROUP)
    if g is None: return []
    tenants = nb.get("tenancy/tenants", tenant_group=g["id"], limit=500)["results"]
    role = nb.one("extras/roles", name=OWNER_ROLE)
    out = []
    for t in sorted(tenants, key=lambda t: (t.get("custom_fields") or {}).get(CF_APP) or ""):
        a = nb.get("extras/contact-associations", associated_object_id=t["id"], limit=10)["results"]
        own = next((x for x in a if role and (x.get("role") or {}).get("id") == role["id"] and x.get("team")), None)
        team = nb.get(f"extras/teams/{own['team']['id']}") if own else None
        app_id = (t.get("custom_fields") or {}).get(CF_APP)
        out.append({"id": t["id"], "app_id": app_id, "name": t["name"], "description": t.get("description") or "",
                    "owner": {"id": team["id"], "name": team["name"], "email": team.get("email") or ""} if team else None,
                    "label": f"{app_id} · {t['name']}"})
    _cache.update(t=time.time(), v=out)
    return out


def invalidate():
    _cache["v"] = None


def save_application(app_id, name, owner, description="", email=""):
    """Create or update an application (tenant) and its owner team. Operators only (the API checks)."""
    ensure_schema(lambda *_: None)
    nb = Nautobot(); g = nb.one("tenancy/tenant-groups", name=GROUP)
    t = next((x for x in nb.get("tenancy/tenants", tenant_group=g["id"], limit=500)["results"] if (x.get("custom_fields") or {}).get(CF_APP) == app_id), None)
    body = {"name": name, "tenant_group": g["id"], "description": description, "custom_fields": {CF_APP: app_id}}
    t = nb.patch(f"tenancy/tenants/{t['id']}", body) if t else nb.post("tenancy/tenants", body)
    team = nb.one("extras/teams", name=owner) or nb.post("extras/teams", {"name": owner, "email": email})
    if email and team.get("email") != email: nb.patch(f"extras/teams/{team['id']}", {"email": email})
    role = nb.one("extras/roles", name=OWNER_ROLE); active = nb.one("extras/statuses", name="Active")
    for a in nb.get("extras/contact-associations", associated_object_id=t["id"], limit=10)["results"]:
        if (a.get("role") or {}).get("id") == role["id"] and (a.get("team") or {}).get("id") != team["id"]:
            nb.delete(f"extras/contact-associations/{a['id']}")
    if not any((a.get("team") or {}).get("id") == team["id"] for a in nb.get("extras/contact-associations", associated_object_id=t["id"], limit=10)["results"]):
        nb.post("extras/contact-associations", {"team": team["id"], "associated_object_type": "tenancy.tenant", "associated_object_id": t["id"],
                                                "role": role["id"], "status": active["id"]})
    invalidate()
    return next(a for a in applications(force=True) if a["app_id"] == app_id)


# ---- profiles -----------------------------------------------------------------------------------------------------------------
def profiles():
    from . import labmodel
    return labmodel.model().get("port_profiles", [])


def profile_summary(p):
    if p["mode"] == "access": return f"access VLAN {p['access_vlan']}"
    s = f"trunk VLANs {', '.join(map(str, p['trunk_vlans']))}"
    return s + (f", untagged {p['native_vlan']}" if p.get("native_vlan") else "")
