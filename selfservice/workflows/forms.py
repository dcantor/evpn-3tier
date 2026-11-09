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
    field_order = ["device", "asn", "peering_vlan", "peering_subnet", "vip_prefixes", "bfd", "reason"]
    device = forms.ChoiceField(label="Load balancer", help_text="A load balancer on the fabric (from Nautobot): it peers with the switch pair it is dual-homed to, in its own VRF.")
    asn = forms.IntegerField(label="Its ASN", min_value=64512, max_value=65534, help_text="A private ASN the lab does not use.")
    peering_vlan = forms.IntegerField(label="Peering VLAN", min_value=2, max_value=4094, help_text="The VLAN the BGP sessions run in (not used in the lab). Its L2VNI will be 10000 + the VLAN ID.")
    peering_subnet = forms.CharField(label="Peering subnet", help_text="e.g. 172.16.150.0/29 — the switches take .1 and .2, the load balancer the last usable address.")
    vip_prefixes = forms.CharField(label="VIP prefixes it may announce", widget=forms.Textarea(attrs={"rows": 3}),
                                   help_text="One per line, e.g. 172.16.200.0/28 — inside 172.16.0.0/16, overlapping no existing subnet. The fabric accepts only these; the load balancer serves the first address of each.")
    bfd = forms.BooleanField(label="BFD on the sessions", required=False, initial=True)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lbs = _model().get("lbs", {})
        self.fields["device"].choices = [(k, f"{k} — {v['pair']} ({', '.join(v['switches'])}), VRF {v['vrf']}" + (" — already peering" if v.get("peering") else ""))
                                         for k, v in lbs.items()]

    def clean_device(self):
        d = self.cleaned_data["device"]; lb = _model()["lbs"][d]
        if lb.get("peering"): raise forms.ValidationError(f"{d} already peers with the fabric (AS {lb['peering']['asn']}).")
        if not lb.get("pair") or not lb.get("vrf"): raise forms.ValidationError(f"{d} is not dual-homed to a switch pair in a VRF (Nautobot).")
        return d

    def clean_asn(self):
        a = self.cleaned_data["asn"]; m = _model()
        if a in m["asns_used"]: raise forms.ValidationError(f"AS {a} is used in the lab ({', '.join(map(str, m['asns_used']))}).")
        return a

    def clean_peering_vlan(self):
        v = self.cleaned_data["peering_vlan"]; m = _model(); held, held_vni = _held_vlans()
        if v in m["vlans_used"] or v in held: raise forms.ValidationError(f"VLAN {v} is in use.")
        if 10000 + v in m["vnis_used"] or 10000 + v in held_vni: raise forms.ValidationError(f"VNI {10000 + v} is in use.")
        return v

    def _net(self, s, what):
        try: net = ipaddress.ip_network(s.strip(), strict=True)
        except ValueError: raise forms.ValidationError(f"{what}: '{s.strip()}' is not a prefix (network address / length).")
        if net.version != 4 or not net.subnet_of(labmodel.FABRIC_SUPERNET): raise forms.ValidationError(f"{what}: {net} must be IPv4 inside {labmodel.FABRIC_SUPERNET}.")
        if clash := _clash(net): raise forms.ValidationError(f"{what}: {net} overlaps {', '.join(clash)}.")
        return net

    def clean_peering_subnet(self):
        net = self._net(self.cleaned_data["peering_subnet"], "peering subnet")
        if not 28 <= net.prefixlen <= 29: raise forms.ValidationError("peering subnet: a /28 or /29 (two switches and the load balancer).")
        return str(net)

    def clean_vip_prefixes(self):
        lines = [l for l in self.cleaned_data["vip_prefixes"].splitlines() if l.strip()]
        if not lines: raise forms.ValidationError("At least one prefix.")
        nets = [self._net(l, "VIP prefix") for l in lines]
        if any(n.prefixlen > 30 for n in nets): raise forms.ValidationError("VIP prefixes: /30 or larger (the first address is the VIP).")
        for i, x in enumerate(nets):
            for y in nets[i + 1:]:
                if x.overlaps(y): raise forms.ValidationError(f"{x} and {y} overlap.")
        return [str(n) for n in nets]

    def clean(self):
        c = super().clean()
        ps, vips = c.get("peering_subnet"), c.get("vip_prefixes") or []
        if ps and any(ipaddress.ip_network(ps).overlaps(ipaddress.ip_network(v)) for v in vips):
            raise forms.ValidationError("The peering subnet and the VIP prefixes overlap.")
        return c

    def params(self):
        p = super().params(); lb = _model()["lbs"][p["device"]]
        p.update(attach_to=lb["pair"], switches=lb["switches"], vrf=lb["vrf"], vni=10000 + p["peering_vlan"]); return p
