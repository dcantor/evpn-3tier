"""The request forms. Each validates against the live lab (labmodel), so a request that reaches an operator already
makes sense: free VLAN IDs and VNIs, subnets inside the fabric's space that overlap nothing, private ASNs the lab does
not use."""
import ipaddress
import re

from django import forms

from . import labmodel
from .allocate import NAME_TEMPLATE

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def _model():
    return labmodel.model()


class RequestForm(forms.Form):
    reason = forms.CharField(label="Why (for the operator)", widget=forms.Textarea(attrs={"rows": 3}), max_length=1000,
                             help_text="What it is for, by when — whatever helps whoever carries it out.")

    def params(self):
        """The request's parameters as stored (JSON-safe)."""
        return {k: v for k, v in self.cleaned_data.items() if k != "reason"}


def _clash(net):
    """What a prefix overlaps: Nautobot's plan, and what open requests hold."""
    from .allocate import claims
    return labmodel.overlaps(net) + [f"{n} ({who})" for n, who in claims()[2] if n.overlaps(net)]


def _held_vlans():
    from .allocate import claims
    v, vni, _ = claims(); return v, vni


class AddVlanForm(RequestForm):
    """The requester picks the VRF and the span; the app allocates the VLAN ID (next unused) and the subnet (phpIPAM's
    first free /24; the gateway is its first host) — allocate.py — and names the VLAN <ID>-<VRF>. The allocated values
    come back with the request and are checked again here (another request may have taken them meanwhile)."""
    field_order = ["vrf", "vlan_id", "gateway", "span", "reason"]
    vrf = forms.ChoiceField(label="VRF", help_text="The tier the VLAN belongs to: hosts in it reach other VRFs through the firewalls.")
    vlan_id = forms.IntegerField(label="VLAN ID", min_value=2, max_value=4094, help_text="Allocated: the next unused VLAN ID. Its L2VNI is 10000 + the VLAN ID.")
    gateway = forms.CharField(label="Gateway / prefix", help_text="Allocated from phpIPAM: the first free /24 in 172.16.0.0/16; the gateway is its first host address.")
    span = forms.ChoiceField(choices=[("leaves", "every leaf"), ("leaves+borders", "every leaf and both border leaves")], initial="leaves",
                             help_text="Borders too when something attached to them (a load balancer, a firewall) needs the VLAN.")
    # how the React front end renders the fields (api.form_schema): the VLAN ID is a button, the rest follow from it
    ui = {"vlan_id": {"type": "allocate", "button": "Select the next unused VLAN ID", "endpoint": "/catalog/add-vlan/allocate",
                      "fills": ["vlan_id", "gateway"], "depends": ["vrf"]},
          "gateway": {"type": "computed", "from": "allocation"}}
    computed = [{"name": "name", "label": "Name", "template": NAME_TEMPLATE, "help": "Automatic: vlan_<VLAN ID>_<VRF>.", "after": "vlan_id"}]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        m = _model(); self.fields["vrf"].choices = [(v, v) for v in m["tier_vrfs"]]

    def clean_vlan_id(self):
        v = self.cleaned_data["vlan_id"]; m = _model(); held, held_vni = _held_vlans()
        if v in m["vlans_used"]: raise forms.ValidationError(f"VLAN {v} is in use (used: {', '.join(map(str, m['vlans_used']))}). Select the next unused one again.")
        if v in held: raise forms.ValidationError(f"VLAN {v} is held by an open request. Select the next unused one again.")
        if 10000 + v in m["vnis_used"] or 10000 + v in held_vni: raise forms.ValidationError(f"VNI {10000 + v} is in use.")
        return v

    def clean_gateway(self):
        g = self.cleaned_data["gateway"].strip()
        try: gw = ipaddress.ip_interface(g)
        except ValueError: raise forms.ValidationError("An IPv4 address with a prefix length, e.g. 172.16.40.1/24.")
        net = gw.network
        if gw.version != 4 or not 16 <= net.prefixlen <= 29: raise forms.ValidationError("IPv4, a prefix between /16 and /29.")
        if gw.ip != net.network_address + 1: raise forms.ValidationError(f"The gateway is the subnet's first host address: {net.network_address + 1}/{net.prefixlen}.")
        if not net.subnet_of(labmodel.FABRIC_SUPERNET): raise forms.ValidationError(f"Inside {labmodel.FABRIC_SUPERNET} (the fabric's tier space).")
        if clash := _clash(net): raise forms.ValidationError(f"{net} overlaps {', '.join(clash)}. Select the next unused VLAN again (a new subnet comes with it).")
        return str(gw)

    def params(self):
        p = super().params()
        from .allocate import vlan_name
        p["name"] = vlan_name(p["vlan_id"], p["vrf"])
        p["vni"] = 10000 + p["vlan_id"]; p["subnet"] = str(ipaddress.ip_interface(p["gateway"]).network); return p


class RemoveVlanForm(RequestForm):
    """Remove a VLAN the self-service app (or an operator) added: never one of the lab's design (lab.conf), one with
    hosts in it, or the last VLAN of its VRF. Everything is re-checked when the worker carries it out."""
    field_order = ["vlan", "confirm", "reason"]
    vlan = forms.ChoiceField(label="VLAN to remove", help_text="From Nautobot's plan. VLANs that cannot be removed say why.")
    confirm = forms.BooleanField(label="I understand: the VLAN, its subnet and gateway go from the switches, Nautobot and phpIPAM", required=True,
                                 help_text="Anything still using the subnet loses its gateway.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["vlan"].choices = [(str(v["vlan"]), f"{v['vlan']} {v['name'] or ''} — VRF {v['vrf']}, {v['subnet']}" + (f" — cannot be removed: {v['blocked']}" if v["blocked"] else ""))
                                       for v in _model().get("vlans", [])]

    def clean_vlan(self):
        vid = int(self.cleaned_data["vlan"]); v = next(x for x in _model()["vlans"] if x["vlan"] == vid)
        if v["blocked"]: raise forms.ValidationError(f"VLAN {vid} cannot be removed: {v['blocked']}.")
        from .models import WorkflowRequest as R
        if R.objects.filter(workflow="remove-vlan", status__in=("submitted", "approved", "running"), parameters__vlan_id=vid).exists():
            raise forms.ValidationError(f"An open request already removes VLAN {vid}.")
        return vid

    def params(self):
        v = next(x for x in _model()["vlans"] if x["vlan"] == self.cleaned_data["vlan"])
        return {"vlan_id": v["vlan"], "name": v["name"] or f"VLAN {v['vlan']}", "vrf": v["vrf"], "vni": v["vni"], "gateway": v["gateway"],
                "subnet": v["subnet"], "on": v["on"]}


class LbPeeringForm(RequestForm):
    """Add a new load balancer pair (highly available, active / standby with keepalived VRRP; dual-homed to the border
    leaves, each member peering with both over eBGP, in the tier the requester picks) or delete the last pair added.
    Everything but the tier is allocated (lbs.py) and shown read-only; no VIPs yet."""
    field_order = ["action", "tier", "reason"]
    action = forms.ChoiceField(label="Load balancer pair", help_text="Load balancers come in pairs (active / standby). Add a new pair on the border leaves' next spare ports, or delete one: only the pairs added here, the last one first; lb1 + lb2 hold the web VIP.")
    tier = forms.ChoiceField(label="Tier (VRF) it serves", required=False,
                             help_text="The pair's addresses and its shared VRRP address go in this VRF's VLAN (extended to the border leaves if it is not there yet). No VIPs are set up at this stage.")
    ui = {"tier": {"when": {"field": "action", "value": "add"}}}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from . import lbs
        m = _model(); self.fields["action"].choices = lbs.choices(m)
        tiers = []
        for v in m["tier_vrfs"]:
            tv = lbs.tier_vlan(m, v)
            if tv: tiers.append((v, f"{v} — VLAN {tv['vlan']}, {tv['subnet']}" + ("" if "borders" in tv["on"] else " (extended to the border leaves)")))
        self.fields["tier"].choices = tiers
        # the plan for each choice, shown read-only under the tier (computed: the front end picks the text by the choices)
        texts = {"": "— choose Add or Delete —", "add": "— choose the tier it serves —"}
        nxt = lbs.next_pair()
        if nxt:
            for v, _ in tiers: texts[f"add|{v}"] = lbs.plan_text(lbs.plan(nxt, v, m))
        else: texts["add"] = "None left: the borders' four spare ports are all used (lb3 + lb4, lb5 + lb6)."
        last = lbs.last_pair()
        for pr in lbs.pairs(m):
            if not pr: continue
            key = f"delete:{'+'.join(pr)}"
            if pr == last:
                es = [next((x for x in m.get("lb_peerings", []) if x["name"] == n), None) for n in pr]
                texts[key] = lbs.delete_text(pr, es) if all(es) else f"{lbs.pair_name(pr)}: no peerings in Nautobot."
            else:
                texts[key] = (f"{lbs.pair_name(pr)} cannot be deleted: " + ("it is part of the lab's design (it holds the web VIP)." if pr == lbs.DESIGN_PAIR or not last or not set(pr) <= set(lbs.added())
                                                                           else f"delete {lbs.pair_name(last)} first."))
        self.computed = [{"name": "plan", "label": "What it will be (allocated)", "after": "tier", "help": "Allocated by Fabric Factory and checked again when the request runs.",
                          "template": "", "keys": ["{action}|{tier}", "{action}"], "map": texts}]

    def clean(self):
        c = super().clean()
        from . import lbs
        act = c.get("action") or ""; m = _model()
        if _open("lb-peering").exists(): raise forms.ValidationError("A load balancer request is already open: one at a time.")
        if act == "add":
            nxt = lbs.next_pair()
            if not nxt: raise forms.ValidationError("The borders' spare ports are all used: at most two pairs can be added (lb3 + lb4, lb5 + lb6).")
            if not c.get("tier"): self.add_error("tier", "Choose the tier it serves."); return c
            pl = lbs.plan(nxt, c["tier"], m)
            if not pl.get("vrrp_address"): self.add_error("tier", f"No three free addresses in VRF {c['tier']}'s VLAN."); return c
            free = lbs.mem_available_mib()
            if free < 2 * lbs.RAM_MIB + 1024: raise forms.ValidationError(f"The host has {free} MiB available: two load balancers need {2 * lbs.RAM_MIB} MiB plus 1 GiB headroom.")
            pl["summary"] = f"Add load balancer pair {pl['pair']} (active / standby) in VRF {pl['vrf']}, peering with the border leaves"
            pl["plan_text"] = lbs.plan_text(pl); c["plan"] = pl
        elif act.startswith("delete:"):
            names = act.split(":", 1)[1].split("+"); last = lbs.last_pair()
            if not all(n in m.get("lbs", {}) for n in names): raise forms.ValidationError(f"There is no load balancer pair {lbs.pair_name(names)}.")
            if names == lbs.DESIGN_PAIR or not set(names) <= set(lbs.added()):
                self.add_error("action", f"{lbs.pair_name(names)} is part of the lab's design (it holds the web VIP): it cannot be deleted."); return c
            if names != last: self.add_error("action", f"Only the last pair added can be deleted: {lbs.pair_name(last)} first."); return c
            es = [next((x for x in m.get("lb_peerings", []) if x["name"] == n), None) for n in names]
            if not all(e and e.get("added") for e in es): raise forms.ValidationError(f"{lbs.pair_name(names)} has no peerings Fabric Factory added in Nautobot.")
            c["plan"] = {"op": "delete", "names": names, "pair": lbs.pair_name(names), "vrf": es[0]["vrf"], "entries": es,
                         "plan_text": lbs.delete_text(names, es), "summary": f"Delete load balancer pair {lbs.pair_name(names)} (VRF {es[0]['vrf']})"}
        else:
            self.add_error("action", "Choose Add or Delete.")
        return c

    def params(self):
        return dict(self.cleaned_data["plan"])


# ---- change a VLAN's span ---------------------------------------------------------------------------------------------------
SPANS = [("leaves", "every leaf"), ("leaves+borders", "every leaf and both border leaves")]


def _open(workflow=None, **params):
    """Open requests (submitted, approved, running), optionally of one workflow / with these parameters."""
    from .models import WorkflowRequest as R
    q = R.objects.filter(status__in=("submitted", "approved", "running"))
    if workflow: q = q.filter(workflow=workflow)
    for k, v in params.items(): q = q.filter(**{f"parameters__{k}": v})
    return q


def _border_hosts(m, vid):
    """VMs on the border leaves whose bond carries this VLAN: the VLAN must stay on the borders for them."""
    borders = set(m["switch_pairs"].get("border leaves", []))
    return [h["name"] for h in m.get("hosts", []) if vid in (h.get("vlans") or []) and borders & set(h.get("peers") or [])]


class VlanSpanForm(RequestForm):
    """Where a tier VLAN lives: on every leaf, or on the leaves and both border leaves (where the firewalls and the load
    balancers attach). Taking the borders away is refused while something attached to them uses the VLAN."""
    field_order = ["vlan", "span", "reason"]
    vlan = forms.ChoiceField(label="VLAN", help_text="A tier VLAN from Nautobot's plan, with where it is now.")
    span = forms.ChoiceField(label="New span", choices=SPANS, help_text="Borders too when something attached to them (a load balancer, a firewall) needs the VLAN.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["vlan"].choices = [(str(v["vlan"]), f"{v['vlan']} {v['name'] or ''} — VRF {v['vrf']}, {v['subnet']}, now on {' + '.join(v['on'])}")
                                       for v in _model().get("vlans", [])]

    def clean(self):
        c = super().clean()
        if "vlan" not in c or "span" not in c: return c
        m = _model(); vid = int(c["vlan"]); v = next(x for x in m["vlans"] if x["vlan"] == vid)
        new = c["span"].split("+")
        if sorted(new) == sorted(v["on"]): raise forms.ValidationError(f"VLAN {vid} is already on {' + '.join(v['on'])}.")
        if "borders" in v["on"] and "borders" not in new and (users := _border_hosts(m, vid)):
            raise forms.ValidationError(f"VLAN {vid} must stay on the border leaves: {', '.join(users)} attached to them use it.")
        if _open(vlan_id=vid).exists(): raise forms.ValidationError(f"An open request already changes VLAN {vid}.")
        c["vlan"] = vid
        return c

    def params(self):
        v = next(x for x in _model()["vlans"] if x["vlan"] == self.cleaned_data["vlan"])
        return {"vlan_id": v["vlan"], "name": v["name"] or f"VLAN {v['vlan']}", "vrf": v["vrf"], "vni": v["vni"], "gateway": v["gateway"],
                "subnet": v["subnet"], "span_from": "+".join(v["on"]), "span_to": self.cleaned_data["span"]}


# ---- custom firewall rules ------------------------------------------------------------------------------------------------------
ZONES = [("outside", "outside (the WAN / internet)"), ("web", "web"), ("app", "app"), ("db", "db")]
PROTOS = [("tcp", "TCP"), ("udp", "UDP"), ("icmp", "ICMP (ping)")]
PORTS = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")
NOT_OUTSIDE = [ipaddress.ip_network(n) for n in ("172.16.0.0/16", "172.31.0.0/16", "10.18.0.0/24", "10.61.0.0/24", "10.255.0.0/16")]


def zone_space(m, zone):
    """The addresses a tier zone owns: its VLANs' subnets, its load balancer VIP ranges, and the web VIP for web."""
    nets = [ipaddress.ip_network(v["subnet"]) for v in m.get("vlans", []) if v["vrf"] == zone]
    nets += [ipaddress.ip_network(x) for p in m.get("lb_peerings", []) if p["vrf"] == zone for x in p["vips"]]
    if zone == "web" and m.get("web_vip"): nets.append(ipaddress.ip_network(m["web_vip"]))
    return nets


def _ports(s):
    m_ = PORTS.match(s or "")
    if not m_: return None
    a, b = int(m_.group(1)), int(m_.group(2) or m_.group(1))
    return (a, b) if 1 <= a <= b <= 65535 else None


def covers(rule, frm, to, proto, dest, port):
    """Does an existing rule already allow this (same zones and protocol, its destination containing ours, its ports ours)?"""
    if rule["from"] != frm or rule["to"] != to or rule["proto"] != proto: return False
    if not ipaddress.ip_network(dest, strict=False).subnet_of(ipaddress.ip_network(rule["dest"], strict=False)): return False
    if proto == "icmp": return True
    have, want = _ports(str(rule.get("port") or "")), _ports(port or "")
    return bool(have and want and have[0] <= want[0] and want[1] <= have[1])


class FwRuleForm(RequestForm):
    """A connection the zone firewall should let start: from one zone to an address or subnet in another, by protocol
    and port. Replies are always allowed; everything not listed stays dropped and logged."""
    field_order = ["source", "target", "proto", "dest", "port", "reason"]
    source = forms.ChoiceField(label="From zone", choices=ZONES, help_text="Where the connection starts: the WAN (outside) or a tier VRF.")
    target = forms.ChoiceField(label="To zone", choices=ZONES, help_text="The zone the destination is in.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        zs = ZONES + [(v, v) for v in _model().get("tier_vrfs", []) if v not in dict(ZONES)]     # + any VRF added
        self.fields["source"].choices = self.fields["target"].choices = zs
    proto = forms.ChoiceField(label="Protocol", choices=PROTOS)
    dest = forms.CharField(label="Destination", max_length=43, help_text="An address or a subnet in the 'to' zone, e.g. 172.16.20.11 or 172.16.20.0/24.")
    port = forms.CharField(label="Port", required=False, max_length=11, help_text="TCP / UDP: a port or a range, e.g. 8443 or 9000-9010. Empty for ICMP.")

    def clean(self):
        c = super().clean()
        frm, to, proto, dest, port = c.get("source"), c.get("target"), c.get("proto"), (c.get("dest") or "").strip(), (c.get("port") or "").strip()
        if not (frm and to and proto and dest): return c
        if frm == to: raise forms.ValidationError("From and to are the same zone: traffic inside a VRF does not cross the firewall.")
        try: net = ipaddress.ip_network(dest, strict=True)
        except ValueError: raise forms.ValidationError(f"Destination '{dest}': an IPv4 address or a subnet (network address / length).")
        if net.version != 4: raise forms.ValidationError("IPv4 only.")
        m = _model()
        if to == "outside":
            if net.prefixlen < 8: raise forms.ValidationError("Outside destinations: /8 or longer (no 'anything').")
            if any(net.overlaps(x) for x in NOT_OUTSIDE): raise forms.ValidationError(f"{net} is inside the lab, not outside.")
        elif not any(net.subnet_of(x) for x in zone_space(m, to)):
            raise forms.ValidationError(f"{net} is not in zone {to}: its subnets are {', '.join(map(str, zone_space(m, to))) or 'none'}.")
        if proto == "icmp":
            if port: raise forms.ValidationError("ICMP has no port: leave it empty.")
            port = None
        else:
            if not _ports(port): raise forms.ValidationError("TCP and UDP need a port (1-65535) or a range (low-high).")
        dest = str(net.network_address) if net.prefixlen == 32 else str(net)
        if hit := next((r for r in m.get("fw_policy", []) if covers(r, frm, to, proto, dest, port)), None):
            raise forms.ValidationError(f"Already allowed by {hit['from']} → {hit['to']} {hit['dest']} {hit['proto']}{' ' + str(hit['port']) if hit.get('port') else ''}"
                                        + (" (a Standard Firewall Rule)" if hit.get("standard") else "") + ".")
        if _open("fw-rule", source=frm, target=to, proto=proto, dest=dest, port=port).exists():
            raise forms.ValidationError("An open request already adds this rule.")
        c.update(dest=dest, port=port)
        return c

    def params(self):
        p = super().params(); p["port_label"] = f"port {p['port']}" if p.get("port") else ""; return p


class FwRuleRemoveForm(RequestForm):
    """Remove a custom rule a request added. The lab's own rules (lab.conf) and the Standard Firewall Rules (they come
    and go with their VLAN) are not removable here."""
    field_order = ["rule", "confirm", "reason"]
    rule = forms.ChoiceField(label="Rule", help_text="Custom rules added through Fabric Factory.")
    confirm = forms.BooleanField(label="I understand: connections it allows stop being accepted (established ones are kept until they end)", required=True)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["rule"].choices = [(f"{r['from']}|{r['to']}|{r['rule']}", rule_label(r)) for r in removable_rules(_model())]

    def clean_rule(self):
        frm, to, num = self.cleaned_data["rule"].split("|")
        r = next((x for x in removable_rules(_model()) if x["from"] == frm and x["to"] == to and str(x["rule"]) == num), None)
        if r is None: raise forms.ValidationError("That rule is no longer in the policy.")
        if _open("fw-rule-remove", source=frm, target=to, rule=int(num)).exists(): raise forms.ValidationError("An open request already removes it.")
        return r

    def params(self):
        r = self.cleaned_data["rule"]
        return {"source": r["from"], "target": r["to"], "proto": r["proto"], "dest": r["dest"], "port": r.get("port"), "rule": r["rule"],
                "port_label": f"port {r['port']}" if r.get("port") else "", "added_by": r.get("request")}


def removable_rules(m):
    return [r for r in m.get("fw_policy", []) if r.get("rule") and not r.get("standard") and not r.get("follows")]


def rule_label(r):
    return (f"{r['from']} → {r['to']} {r['dest']} {r['proto']}{' ' + str(r['port']) if r.get('port') else ''} — rule {r['rule']}"
            + (f", request #{r['request']}" if r.get("request") else ""))


# ---- move a server to another VLAN --------------------------------------------------------------------------------------------
MOVABLE = ("web", "app")
NOT_MOVABLE = {"db": "database servers keep their replication peers' addresses: not automated yet",
               "lb": "load balancers hang off the border leaves and carry the web VIP: not automated"}


class MoveServerForm(RequestForm):
    """Move a web or app server to another VLAN of its own VRF (on the leaves). It keeps its host number in the new
    subnet (web1, .11, in 172.16.6.0/24 becomes 172.16.6.11). Its tier keeps serving: the other server carries the
    traffic while it moves."""
    field_order = ["server", "vlan", "reason"]
    server = forms.ChoiceField(label="Server", help_text="Web and app servers can move; the list says why others cannot.")
    vlan = forms.ChoiceField(label="To VLAN", help_text="A VLAN of the server's VRF that is on the leaves.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        m = _model()
        self.fields["server"].choices = [(h["name"], f"{h['name']} — VRF {h['vrf']}, VLAN {h['vlan']}, {h['address']}"
                                          + (f" — cannot move: {NOT_MOVABLE[h['role']]}" if h["role"] in NOT_MOVABLE else ""))
                                         for h in m.get("hosts", [])]
        self.fields["vlan"].choices = [(str(v["vlan"]), f"{v['vlan']} {v['name'] or ''} — VRF {v['vrf']}, {v['subnet']}")
                                       for v in m.get("vlans", []) if "leaves" in v["on"]]

    def clean(self):
        c = super().clean()
        if "server" not in c or "vlan" not in c: return c
        m = _model(); h = next(x for x in m["hosts"] if x["name"] == c["server"]); vid = int(c["vlan"])
        v = next(x for x in m["vlans"] if x["vlan"] == vid)
        if h["role"] in NOT_MOVABLE: raise forms.ValidationError(f"{h['name']}: {NOT_MOVABLE[h['role']]}.")
        if vid == h["vlan"]: raise forms.ValidationError(f"{h['name']} is already in VLAN {vid}.")
        if v["vrf"] != h["vrf"]: raise forms.ValidationError(f"VLAN {vid} is in VRF {v['vrf']}; {h['name']} is in VRF {h['vrf']}. A server moves within its VRF (its tier's zone).")
        if len(h.get("vlans") or []) > 1: raise forms.ValidationError(f"{h['name']} is in the middle of a move (its bond carries VLANs {h['vlans']}).")
        net = ipaddress.ip_network(v["subnet"]); addr = net.network_address + int(h["hostnum"])
        if addr not in net or addr in (net.network_address, net.broadcast_address, ipaddress.ip_interface(v["gateway"]).ip):
            raise forms.ValidationError(f"Host number {h['hostnum']} does not fit {net}.")
        used = {ipaddress.ip_interface(x["address"]).ip for x in m["hosts"] if x.get("address")}
        if addr in used: raise forms.ValidationError(f"{addr} is taken by another VM.")
        if _open("move-server").exists(): raise forms.ValidationError("Another server move is open: one at a time.")
        c.update(vlan=vid, _host=h, _vlan=v, _addr=f"{addr}/{net.prefixlen}")
        return c

    def params(self):
        c = self.cleaned_data; h, v = c["_host"], c["_vlan"]
        return {"server": h["name"], "role": h["role"], "vrf": h["vrf"], "hostnum": h["hostnum"],
                "from_vlan": h["vlan"], "from_address": h["address"], "to_vlan": v["vlan"], "to_name": v["name"],
                "to_subnet": v["subnet"], "to_address": c["_addr"], "to_gateway": v["gateway"]}


# ---- port provisioning --------------------------------------------------------------------------------------------------------
PORT_STATES = [("enabled", "Enabled"), ("disabled", "Disabled (shut down; keeps its configuration)"),
               ("default", "Default: disabled, no configuration (spare ports)")]
PORT_MODES = [("", "— leave as it is (or from the profile) —"), ("access", "Access: one VLAN, untagged"), ("tagged", "Trunk: chosen VLANs, tagged")]
UPLINK = "uplinks to the spines carry the whole fabric: they cannot be changed here"
SPARE_DESC = "spare port (Port Provisioning)"


def leaf_vlans(m):
    """The VLANs a leaf port may carry: tier VLANs on the leaves."""
    return [v for v in m.get("vlans", []) if "leaves" in v["on"]]


def port_partner(m, p):
    """The other leg of a server's bond: same server, the other leaf."""
    return next((x for x in m.get("ports", []) if x["kind"] == "access" and x["peer"] == p["peer"] and x["switch"] != p["switch"]), None)


def port_cfg(mode, access, trunk, native=None):
    if mode == "access": return f"access VLAN {access}"
    if mode == "tagged": return f"trunk VLANs {', '.join(map(str, trunk))}" + (f", untagged {native}" if native else "")
    return "no VLANs"


def port_label(p):
    state = "enabled" if p["enabled"] else "disabled"
    if p["kind"] == "fabric": return f"{p['switch']} {p['name']} — uplink to {p['peer']}: blocked"
    if p["kind"] == "access": return f"{p['switch']} {p['name']} — {p['peer']}'s bond leg ({state})"
    cfg = port_cfg(p["mode"], p["untagged_vlan"], p["tagged_vlans"], p["untagged_vlan"] if p["mode"] == "tagged" else None)
    who = f", {p['application']['app_id']}" if p.get("application") else ""
    prof = f", profile {p['profile']}" if p.get("profile") else ""
    return f"{p['switch']} {p['name']} — spare port ({state}, {cfg}{prof}{who})"


def port_summary(state, mode, access, trunk, native=None):
    s = {"enabled": "enabled", "disabled": "disabled", "default": "default (disabled, no configuration)"}[state]
    return s if state == "default" or not mode else f"{s}, {port_cfg(mode, access, trunk, native)}"


def compose_description(app, text):
    """What the switch shows: '<APP-ID> <application> — <what is plugged in>'."""
    text = (text or "").strip()
    if not app: return text or SPARE_DESC
    return f"{app['app_id']} {app['name']}" + (f" — {text}" if text and not text.startswith(app["app_id"]) else "")


class PortForm(RequestForm):
    """A leaf port: enable it, disable it, or put it back to its default (disabled, no configuration). A spare port gets
    its configuration from a port profile or by hand — access (one VLAN, untagged) or trunk (VLANs tagged, optionally one
    untagged) — and an owner: the application it belongs to. Uplinks to the spines are refused. A server's bond leg can
    only be enabled or disabled — disabled only while its other leg is up — and its VLAN follows the server."""
    field_order = ["port", "state", "profile", "mode", "access_vlan", "trunk_vlans", "native_vlan", "application", "description", "reason"]
    port = forms.ChoiceField(label="Port", help_text="Every leaf port. Uplinks to the spines are listed but blocked; border leaves and spines are not offered.")
    state = forms.ChoiceField(label="Action", choices=PORT_STATES, help_text="Default takes a spare port back to disabled with no VLANs, profile, owner or description.")
    profile = forms.ChoiceField(label="Port profile", required=False, help_text="A named preset made by the operators: it sets the mode and the VLANs. Or leave it empty and set them by hand.")
    mode = forms.ChoiceField(label="Mode", choices=PORT_MODES, required=False, help_text="Spare ports only, when no profile is chosen.")
    access_vlan = forms.ChoiceField(label="Access VLAN", required=False, help_text="Access mode: the one VLAN, untagged.")
    trunk_vlans = forms.MultipleChoiceField(label="Trunk VLANs", required=False, help_text="Trunk mode: the VLANs allowed, tagged. Only VLANs the leaves carry.")
    native_vlan = forms.ChoiceField(label="Trunk untagged (native) VLAN", required=False, help_text="Trunk mode, optional: one VLAN carried untagged.")
    application = forms.ChoiceField(label="Application (owner)", required=False, help_text="Who owns the port: an application and its owner team. Required to enable a spare port.")
    description = forms.CharField(label="Description", required=False, max_length=60, help_text="What is plugged in, e.g. 'backup appliance'. The switch shows '<APP-ID> <application> — <this>'.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from . import ownership
        m = _model()
        self.fields["port"].choices = [(f"{p['switch']}|{p['name']}", port_label(p)) for p in m.get("ports", [])]
        vl = [(str(v["vlan"]), f"{v['vlan']} {v['name'] or ''} ({v['vrf']}, {v['subnet']})") for v in leaf_vlans(m)]
        self.fields["access_vlan"].choices = [("", "—")] + vl
        self.fields["native_vlan"].choices = [("", "— none —")] + vl
        self.fields["trunk_vlans"].choices = vl
        self.fields["profile"].choices = [("", "— none: set it by hand —")] + [(p["name"], f"{p['name']} — {ownership.profile_summary(p)}") for p in m.get("port_profiles", [])]
        try: apps = ownership.applications()
        except Exception: apps = []                                          # noqa: BLE001
        self.fields["application"].choices = [("", "—")] + [(a["app_id"], a["label"] + (f" (owner: {a['owner']['name']})" if a.get("owner") else "")) for a in apps]
        self._apps = {a["app_id"]: a for a in apps}

    def clean(self):
        c = super().clean()
        if "port" not in c or "state" not in c: return c
        m = _model(); sw, name = c["port"].split("|")
        p = next((x for x in m["ports"] if x["switch"] == sw and x["name"] == name), None)
        if p is None: raise forms.ValidationError(f"{sw} {name} is not a leaf port in Nautobot.")
        if p["kind"] == "fabric": raise forms.ValidationError(f"{sw} {name} is an uplink to {p['peer']}: {UPLINK}.")
        if p["kind"] not in (None, "access"): raise forms.ValidationError(f"{sw} {name} ({p['kind']}) cannot be provisioned here.")
        state = c["state"]; mode = c.get("mode") or ""; prof_name = c.get("profile") or ""
        access = c.get("access_vlan") or ""; trunk = sorted(int(v) for v in c.get("trunk_vlans") or []); native = c.get("native_vlan") or ""
        app_id = c.get("application") or ""; app = self._apps.get(app_id) if app_id else None
        manual = bool(mode or access or trunk or native)
        prof = None
        if p["kind"] == "access":
            if state == "default": raise forms.ValidationError(f"{sw} {name} is {p['peer']}'s bond leg: its default is cabled and enabled — use Enable.")
            if manual or prof_name or app_id:
                raise forms.ValidationError(f"{sw} {name} is {p['peer']}'s bond leg: its VLAN and owner follow the server — only enable or disable it here (Move a server changes the VLAN).")
            if state == "disabled":
                other = port_partner(m, p)
                if other is None or not other["enabled"]:
                    raise forms.ValidationError(f"Disabling {sw} {name} would cut {p['peer']} off: its other leg ({other['switch'] + ' ' + other['name'] if other else 'none'}) is down.")
            mode, access_v, trunk_v, native_v, app, prof_name = p["mode"], p["untagged_vlan"], p["tagged_vlans"], None, None, None
        elif state == "default":
            if manual or prof_name or app_id: raise forms.ValidationError("Default clears the port: leave the profile, mode, VLANs and application empty.")
            mode, access_v, trunk_v, native_v, app, prof_name = None, None, [], None, None, None
        else:
            if prof_name:
                if manual: raise forms.ValidationError(f"Profile {prof_name} sets the mode and the VLANs: leave them empty, or choose no profile.")
                prof = next((x for x in m.get("port_profiles", []) if x["name"] == prof_name), None)
                if prof is None: raise forms.ValidationError(f"There is no profile {prof_name}.")
                mode, access_v, trunk_v, native_v = prof["mode"], prof.get("access_vlan"), list(prof.get("trunk_vlans") or []), prof.get("native_vlan")
            elif not mode:                                                   # keep what the port has
                mode, access_v, trunk_v = p["mode"], p["untagged_vlan"] if p["mode"] == "access" else None, p["tagged_vlans"]
                native_v = p["untagged_vlan"] if p["mode"] == "tagged" else None
                prof_name = p.get("profile")
                if state == "enabled" and not mode: raise forms.ValidationError("An enabled spare port needs a configuration: a profile, or a mode (access or trunk).")
            elif mode == "access":
                if not access: raise forms.ValidationError("Access mode: choose the access VLAN.")
                if trunk or native: raise forms.ValidationError("Access mode carries one VLAN: leave the trunk VLANs and the native VLAN empty.")
                access_v, trunk_v, native_v = int(access), [], None
            else:
                if not trunk: raise forms.ValidationError("Trunk mode: choose at least one VLAN.")
                if access: raise forms.ValidationError("Trunk mode: leave the access VLAN empty (use the native VLAN for one untagged VLAN).")
                access_v, trunk_v, native_v = None, trunk, int(native) if native else None
                if native_v in trunk_v: raise forms.ValidationError(f"VLAN {native_v} cannot be both tagged and the untagged native VLAN.")
            ok = {v["vlan"] for v in leaf_vlans(m)}
            bad = [v for v in ([access_v] if mode == "access" else trunk_v + ([native_v] if native_v else []) if mode == "tagged" else []) if v not in ok]
            if bad: raise forms.ValidationError(f"VLAN(s) {', '.join(map(str, bad))} are not on the leaves.")
            if app is None and not app_id: app = p.get("application")          # keep the owner it has
            if state == "enabled" and app is None: raise forms.ValidationError("An enabled spare port needs an owner: choose its application.")
        enabled = state == "enabled"
        untagged = access_v if mode == "access" else native_v if mode == "tagged" else None
        desc = compose_description(app, c.get("description") or ("" if state == "default" else (p["description"].split(" — ", 1)[-1] if p.get("application") else
                                                                                                  ("" if p["description"] == SPARE_DESC else p["description"]))))
        if p["kind"] == "access": desc = p["description"]
        now = (p["enabled"], p["mode"], p["untagged_vlan"], p["tagged_vlans"] if p["mode"] == "tagged" else [], p.get("profile"), (p.get("application") or {}).get("app_id"), p["description"])
        new = (enabled, mode or None, untagged, trunk_v if mode == "tagged" else [], prof_name or None, (app or {}).get("app_id"), desc)
        if now == new: raise forms.ValidationError(f"{sw} {name} is already {port_summary(state, mode, access_v, trunk_v, native_v)}: nothing to change.")
        if _open("port", switch=sw, port=name).exists(): raise forms.ValidationError(f"An open request already changes {sw} {name}.")
        c.update(_p=p, _mode=mode or None, _untagged=untagged, _trunk=trunk_v if mode == "tagged" else [], _native=native_v, _access=access_v,
                 _profile=prof_name or None, _app=app, _desc=desc)
        return c

    def params(self):
        c = self.cleaned_data; p = c["_p"]; m = _model(); other = port_partner(m, p) if p["kind"] == "access" else None
        app = c["_app"]
        return {"switch": p["switch"], "port": p["name"], "kind": "server leg" if p["kind"] == "access" else "spare", "peer": p["peer"],
                "action": c["state"], "enabled": c["state"] == "enabled", "mode": c["_mode"], "access_vlan": c["_access"] if c["_mode"] == "access" else None,
                "trunk_vlans": c["_trunk"], "native_vlan": c["_native"] if c["_mode"] == "tagged" else None, "untagged_vlan": c["_untagged"],
                "profile": c["_profile"], "application": app and {k: app[k] for k in ("id", "app_id", "name")}, "owner": app and app.get("owner") and app["owner"]["name"],
                "description": c["_desc"], "partner": f"{other['switch']} {other['name']}" if other else None,
                "before": port_summary("enabled" if p["enabled"] else "disabled", p["mode"], p["untagged_vlan"], p["tagged_vlans"],
                                       p["untagged_vlan"] if p["mode"] == "tagged" else None),
                "after": port_summary(c["state"], c["_mode"], c["_access"], c["_trunk"], c["_native"])
                         + (f", profile {c['_profile']}" if c["_profile"] and c["state"] != "default" else "")
                         + (f", {app['app_id']}" if app and c["state"] != "default" else "")}


class PortProfileForm(RequestForm):
    """Create, change or delete a port profile: a named preset for spare ports (mode, VLANs, optional native VLAN).
    Changing one can re-apply it to every port that uses it, in the same request; one in use cannot be deleted."""
    field_order = ["action", "name", "description", "mode", "access_vlan", "trunk_vlans", "native_vlan", "reapply", "reason"]
    action = forms.ChoiceField(label="Action", choices=[("create", "Create a profile"), ("update", "Change a profile"), ("delete", "Delete a profile")])
    name = forms.CharField(label="Name", max_length=30, help_text="Lower case, digits and dashes, e.g. web-access. For a change or a delete: an existing profile.")
    description = forms.CharField(label="Description", required=False, max_length=80)
    mode = forms.ChoiceField(label="Mode", required=False, choices=[("", "—"), ("access", "Access: one VLAN, untagged"), ("tagged", "Trunk: chosen VLANs, tagged")])
    access_vlan = forms.ChoiceField(label="Access VLAN", required=False)
    trunk_vlans = forms.MultipleChoiceField(label="Trunk VLANs", required=False)
    native_vlan = forms.ChoiceField(label="Trunk untagged (native) VLAN", required=False)
    reapply = forms.BooleanField(label="Re-apply a changed profile to every port that uses it", required=False, initial=True)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        vl = [(str(v["vlan"]), f"{v['vlan']} {v['name'] or ''} ({v['vrf']}, {v['subnet']})") for v in leaf_vlans(_model())]
        self.fields["access_vlan"].choices = [("", "—")] + vl
        self.fields["native_vlan"].choices = [("", "— none —")] + vl
        self.fields["trunk_vlans"].choices = vl

    def clean(self):
        c = super().clean()
        if "action" not in c or not c.get("name"): return c
        m = _model(); name = c["name"].strip().lower(); act = c["action"]
        if not SLUG.match(name): raise forms.ValidationError("Name: lower case letters, digits and dashes, starting with a letter (2-31).")
        profs = {p["name"]: p for p in m.get("port_profiles", [])}
        users = [f"{p['switch']} {p['name']}" for p in m.get("ports", []) if p.get("profile") == name]
        if act == "create" and name in profs: raise forms.ValidationError(f"Profile {name} exists: choose Change.")
        if act in ("update", "delete") and name not in profs: raise forms.ValidationError(f"There is no profile {name}.")
        if act == "delete":
            if users: raise forms.ValidationError(f"Profile {name} is in use on {', '.join(users)}: give those ports another profile (or Default them) first.")
            c.update(name=name, _prof=profs[name], _users=[]); return c
        mode = c.get("mode") or ""; access = c.get("access_vlan") or ""; trunk = sorted(int(v) for v in c.get("trunk_vlans") or []); native = c.get("native_vlan") or ""
        if mode == "access":
            if not access or trunk or native: raise forms.ValidationError("Access: one access VLAN, no trunk or native VLANs.")
            prof = {"name": name, "description": c.get("description") or "", "mode": "access", "access_vlan": int(access), "trunk_vlans": [], "native_vlan": None}
        elif mode == "tagged":
            if not trunk or access: raise forms.ValidationError("Trunk: at least one trunk VLAN, no access VLAN.")
            if native and int(native) in trunk: raise forms.ValidationError(f"VLAN {native} cannot be both tagged and the native VLAN.")
            prof = {"name": name, "description": c.get("description") or "", "mode": "tagged", "access_vlan": None, "trunk_vlans": trunk, "native_vlan": int(native) if native else None}
        else:
            raise forms.ValidationError("Choose the mode: access or trunk.")
        if act == "update" and prof == profs[name]: raise forms.ValidationError(f"Profile {name} is already exactly this: nothing to change.")
        if _open("port-profile", name=name).exists() or _open("port").filter(parameters__profile=name).exists():
            raise forms.ValidationError(f"An open request already uses or changes profile {name}.")
        c.update(name=name, _prof=prof, _users=users if act == "update" and c.get("reapply") else [])
        return c

    def params(self):
        from .ownership import profile_summary
        c = self.cleaned_data
        return {"action": c["action"], "name": c["name"], "profile": c["_prof"], "ports": c["_users"],
                "summary": {"create": "create", "update": "change to", "delete": "delete"}[c["action"]] + ("" if c["action"] == "delete" else " " + profile_summary(c["_prof"]))
                           + (f", re-applied to {len(c['_users'])} port(s)" if c["_users"] else "")}


class AddLeafPairForm(RequestForm):
    """Add a leaf pair: a new rack of two leaves, cabled to both spines. Everything is allocated: the next leaf numbers, AS
    numbers, addresses and the spines' next spare ports (leafpair.py)."""
    field_order = ["pair", "reason"]
    pair = forms.ChoiceField(label="The next leaf pair", help_text="Allocated: the next leaf numbers and rack, their AS numbers, management and loopback addresses, and the spines' next spare ports.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from . import leafpair
        nxt = leafpair.next_pair()
        self.fields["pair"].choices = [(",".join(str(x["k"]) for x in nxt), leafpair.pair_label(nxt))] if nxt else [("", "— none: the spines have no spare ports left —")]

    def clean(self):
        c = super().clean()
        from . import leafpair
        nxt = leafpair.next_pair()
        if not nxt: raise forms.ValidationError("The spines have no spare ports left: at most two pairs can be added (leaf5-leaf8).")
        if c.get("pair") != ",".join(str(x["k"]) for x in nxt): raise forms.ValidationError("The next pair changed: reload the form.")
        if _open("add-leaf-pair").exists() or _open("remove-leaf-pair").exists(): raise forms.ValidationError("A leaf-pair request is already open.")
        free = leafpair.mem_available_mib()
        if free < 2 * leafpair.RAM_MIB + 2048: raise forms.ValidationError(f"The host has {free} MiB available: two leaves need {2 * leafpair.RAM_MIB} MiB plus 2 GiB headroom.")
        c.update(ks=[x["k"] for x in nxt], leaves=" + ".join(x["name"] for x in nxt), rack=nxt[0]["rack"])
        return c


class RemoveLeafPairForm(RequestForm):
    """Remove the last leaf pair added. Refused while a server is cabled to it or a spare port is provisioned."""
    field_order = ["pair", "confirm", "reason"]
    pair = forms.ChoiceField(label="Leaf pair", help_text="Only the last pair added can be removed (the leaves keep contiguous numbers, so no address moves).")
    confirm = forms.CharField(label="Type the rack name to confirm", max_length=20)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from . import leafpair
        last = leafpair.last_pair()
        self.fields["pair"].choices = [(",".join(str(x["k"]) for x in last), leafpair.pair_label(last))] if last else [("", "— none: no pair has been added —")]

    def clean(self):
        c = super().clean()
        from . import leafpair
        last = leafpair.last_pair()
        if not last: raise forms.ValidationError("No leaf pair has been added: the original leaves cannot be removed.")
        if c.get("pair") != ",".join(str(x["k"]) for x in last): raise forms.ValidationError("The last pair changed: reload the form.")
        if (c.get("confirm") or "").strip() != last[0]["rack"]: raise forms.ValidationError(f"Type {last[0]['rack']} to confirm.")
        if _open("add-leaf-pair").exists() or _open("remove-leaf-pair").exists(): raise forms.ValidationError("A leaf-pair request is already open.")
        names = [x["name"] for x in last]
        if _open("port").filter(parameters__switch__in=names).exists(): raise forms.ValidationError("An open Port Provisioning request uses this pair.")
        why = leafpair.in_use(names)
        if why: raise forms.ValidationError("The pair is in use: " + "; ".join(why) + ". Remove the servers / Default the ports first.")
        c.update(ks=[x["k"] for x in last], leaves=" + ".join(names), rack=last[0]["rack"])
        return c


class AddVrfForm(RequestForm):
    """Add a VRF: a tier zone behind the firewalls. The requester names it; everything else is allocated (vrfs.py). With a
    first VLAN (the default) the zone is usable at once: the VLAN gets the next unused ID and phpIPAM's first free /24."""
    field_order = ["vrf", "first_vlan", "span", "reason"]
    vrf = forms.CharField(label="VRF name", max_length=8, help_text="2-8 lower-case letters and digits, starting with a letter, e.g. pay. It names the firewall zone too (PAY).")
    first_vlan = forms.BooleanField(label="Create its first VLAN in the same request", required=False, initial=True,
                                    help_text="The next unused VLAN ID and phpIPAM's first free /24, with an anycast gateway — so the VRF reaches the leaves at once.")
    span = forms.ChoiceField(label="First VLAN on", choices=[("leaves", "every leaf"), ("leaves+borders", "every leaf and both border leaves")], initial="leaves", required=False)

    def clean(self):
        c = super().clean()
        if not c.get("vrf"): return c
        from . import allocate, vrfs
        name = c["vrf"].strip().lower(); m = _model()
        from .clients import Nautobot
        data = Nautobot().context()["data"]
        if not vrfs.NAME.match(name): raise forms.ValidationError("VRF name: 2-8 lower-case letters and digits, starting with a letter.")
        if name in vrfs.RESERVED: raise forms.ValidationError(f"{name} is reserved.")
        if _open("add-vrf").exists() or _open("delete-vrf").exists(): raise forms.ValidationError("A VRF request is already open: one at a time.")
        z = vrfs.next_z(data)
        if z is None: raise forms.ValidationError(f"{vrfs.MAX_ADDED} VRFs have been added already: the limit.")
        a = vrfs.allocation(z, name)
        if why := vrfs.clashes(a, m, data): raise forms.ValidationError("; ".join(why) + ".")
        c.update(vrf=name, z=z, summary=vrfs.label(a))
        if c.get("first_vlan"):
            vid, net = allocate.next_vlan(m), allocate.next_subnet(m)
            c.update(vlan_id=vid, vni=10000 + vid, subnet=str(net), gateway=f"{net.network_address + 1}/{net.prefixlen}",
                     name=allocate.vlan_name(vid, name), span=c.get("span") or "leaves")
            c["summary"] += f"; first VLAN {vid} ({net})"
        else:
            c.pop("span", None)
        return c


class DeleteVrfForm(RequestForm):
    """Delete a VRF added by Fabric Factory. Refused while it holds anything: VLANs, servers, LB peerings, custom rules."""
    field_order = ["vrf", "confirm", "reason"]
    vrf = forms.ChoiceField(label="VRF", help_text="Only VRFs added by Fabric Factory; web, app, db and outside are the lab's design.")
    confirm = forms.CharField(label="Type the VRF name to confirm", max_length=8)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from . import vrfs
        from .clients import Nautobot
        self._data = Nautobot().context()["data"]
        self.fields["vrf"].choices = [(v["name"], f"{v['name']} (L3VNI {v['l3vni']})") for v in vrfs.added(self._data)] or [("", "— none: no VRF has been added —")]

    def clean(self):
        c = super().clean()
        from . import vrfs
        mine = next((v for v in vrfs.added(self._data) if v["name"] == c.get("vrf")), None)
        if not mine: raise forms.ValidationError("Choose a VRF added by Fabric Factory.")
        if (c.get("confirm") or "").strip() != mine["name"]: raise forms.ValidationError(f"Type {mine['name']} to confirm.")
        if _open("add-vrf").exists() or _open("delete-vrf").exists(): raise forms.ValidationError("A VRF request is already open: one at a time.")
        if why := vrfs.in_use(mine["name"], _model(), self._data):
            raise forms.ValidationError(f"VRF {mine['name']} is in use: {', '.join(why)}. Remove those first.")
        c.update(z=mine["z"], summary=vrfs.label(vrfs.allocation(mine["z"], mine["name"])))
        return c
