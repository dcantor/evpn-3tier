"""The request forms. Each validates against the live lab (labmodel), so a request that reaches an operator already
makes sense: free VLAN IDs and VNIs, subnets inside the fabric's space that overlap nothing, private ASNs the lab does
not use."""
import ipaddress
import re

from django import forms

from . import labmodel

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def _model():
    return labmodel.model()


class RequestForm(forms.Form):
    reason = forms.CharField(label="Why (for the operator)", widget=forms.Textarea(attrs={"rows": 3}), max_length=1000,
                             help_text="What it is for, by when — whatever helps whoever carries it out.")

    def params(self):
        """The request's parameters as stored (JSON-safe)."""
        return {k: v for k, v in self.cleaned_data.items() if k != "reason"}


class AddVlanForm(RequestForm):
    field_order = ["vlan_id", "name", "vrf", "gateway", "span", "reason"]
    vlan_id = forms.IntegerField(label="VLAN ID", min_value=2, max_value=4094, help_text="2-4094, not used in the lab. The L2VNI will be 10000 + the VLAN ID.")
    name = forms.CharField(max_length=31, help_text="lower case, e.g. app-backup")
    vrf = forms.ChoiceField(label="VRF", help_text="The tier the VLAN belongs to: hosts in it reach other VRFs through the firewalls.")
    gateway = forms.CharField(label="Gateway / prefix", help_text="The anycast gateway with its prefix length, e.g. 172.16.40.1/24 — inside 172.16.0.0/16, overlapping nothing.")
    span = forms.ChoiceField(choices=[("leaves", "every leaf"), ("leaves+borders", "every leaf and both border leaves")], initial="leaves",
                             help_text="Borders too when something attached to them (a load balancer, a firewall) needs the VLAN.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        m = _model(); self.fields["vrf"].choices = [(v, v) for v in m["tier_vrfs"]]

    def clean_vlan_id(self):
        v = self.cleaned_data["vlan_id"]; m = _model()
        if v in m["vlans_used"]: raise forms.ValidationError(f"VLAN {v} is in use (used: {', '.join(map(str, m['vlans_used']))}).")
        if 10000 + v in m["vnis_used"]: raise forms.ValidationError(f"VNI {10000 + v} is in use.")
        return v

    def clean_name(self):
        n = self.cleaned_data["name"].strip().lower()
        if not SLUG.match(n): raise forms.ValidationError("Lower-case letters, digits and dashes, starting with a letter.")
        return n

    def clean_gateway(self):
        g = self.cleaned_data["gateway"].strip()
        try: gw = ipaddress.ip_interface(g)
        except ValueError: raise forms.ValidationError("An IPv4 address with a prefix length, e.g. 172.16.40.1/24.")
        net = gw.network
        if gw.version != 4 or not 16 <= net.prefixlen <= 29: raise forms.ValidationError("IPv4, a prefix between /16 and /29.")
        if gw.ip in (net.network_address, net.broadcast_address): raise forms.ValidationError("The gateway must be a host address of the subnet.")
        if not net.subnet_of(labmodel.FABRIC_SUPERNET): raise forms.ValidationError(f"Inside {labmodel.FABRIC_SUPERNET} (the fabric's tier space).")
        if clash := labmodel.overlaps(net): raise forms.ValidationError(f"{net} overlaps {', '.join(clash)}.")
        return str(gw)

    def params(self):
        p = super().params(); p["vni"] = 10000 + p["vlan_id"]; p["subnet"] = str(ipaddress.ip_interface(p["gateway"]).network); return p


class LbPeeringForm(RequestForm):
    field_order = ["name", "attach_to", "vrf", "asn", "peering_vlan", "peering_subnet", "vip_prefixes", "bfd", "reason"]
    name = forms.CharField(max_length=31, help_text="The load balancer's name, e.g. lb3")
    attach_to = forms.ChoiceField(label="Attach to", help_text="The switch pair it is dual-homed to (EVPN multihoming) and peers with.")
    vrf = forms.ChoiceField(label="VRF")
    asn = forms.IntegerField(label="Its ASN", min_value=64512, max_value=65534, help_text="A private ASN the lab does not use.")
    peering_vlan = forms.IntegerField(label="Peering VLAN", min_value=2, max_value=4094, help_text="The VLAN the BGP sessions run in (not used in the lab).")
    peering_subnet = forms.CharField(label="Peering subnet", help_text="e.g. 172.16.150.0/29 — the switches take the first addresses, the load balancer the last.")
    vip_prefixes = forms.CharField(label="VIP prefixes it may announce", widget=forms.Textarea(attrs={"rows": 3}),
                                   help_text="One per line, e.g. 172.16.200.0/28 — inside 172.16.0.0/16, overlapping no existing subnet. The fabric accepts only these.")
    bfd = forms.BooleanField(label="BFD on the sessions", required=False, initial=True)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        m = _model()
        self.fields["attach_to"].choices = [(k, f"{k} ({', '.join(v)})") for k, v in m["switch_pairs"].items()]
        self.fields["vrf"].choices = [(v, v) for v in m["vrfs"]]

    def clean_name(self):
        n = self.cleaned_data["name"].strip().lower()
        if not SLUG.match(n): raise forms.ValidationError("Lower-case letters, digits and dashes, starting with a letter.")
        return n

    def clean_asn(self):
        a = self.cleaned_data["asn"]; m = _model()
        if a in m["asns_used"]: raise forms.ValidationError(f"AS {a} is used in the lab ({', '.join(map(str, m['asns_used']))}).")
        return a

    def clean_peering_vlan(self):
        v = self.cleaned_data["peering_vlan"]
        if v in _model()["vlans_used"]: raise forms.ValidationError(f"VLAN {v} is in use.")
        return v

    def _net(self, s, what):
        try: net = ipaddress.ip_network(s.strip(), strict=True)
        except ValueError: raise forms.ValidationError(f"{what}: '{s.strip()}' is not a prefix (network address / length).")
        if net.version != 4 or not net.subnet_of(labmodel.FABRIC_SUPERNET): raise forms.ValidationError(f"{what}: {net} must be IPv4 inside {labmodel.FABRIC_SUPERNET}.")
        if clash := labmodel.overlaps(net): raise forms.ValidationError(f"{what}: {net} overlaps {', '.join(clash)}.")
        return net

    def clean_peering_subnet(self):
        net = self._net(self.cleaned_data["peering_subnet"], "peering subnet")
        if not 28 <= net.prefixlen <= 30: raise forms.ValidationError("peering subnet: a /28 to /30 (two switches and the load balancer).")
        return str(net)

    def clean_vip_prefixes(self):
        lines = [l for l in self.cleaned_data["vip_prefixes"].splitlines() if l.strip()]
        if not lines: raise forms.ValidationError("At least one prefix.")
        nets = [self._net(l, "VIP prefix") for l in lines]
        for i, a in enumerate(nets):
            for b in nets[i + 1:]:
                if a.overlaps(b): raise forms.ValidationError(f"{a} and {b} overlap.")
        return [str(n) for n in nets]

    def clean(self):
        c = super().clean()
        ps, vips = c.get("peering_subnet"), c.get("vip_prefixes") or []
        if ps and any(ipaddress.ip_network(ps).overlaps(ipaddress.ip_network(v)) for v in vips):
            raise forms.ValidationError("The peering subnet and the VIP prefixes overlap.")
        return c
