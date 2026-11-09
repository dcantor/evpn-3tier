"""The workflow catalog: what a user can request. Each entry has a form (forms.py) that validates the request against
the live lab, a title template, and a description of what executing it will do (later: a pipeline run on Gitea, a
Nautobot change). Add a workflow = add an entry here and its form."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Workflow:
    slug: str
    name: str
    summary: str
    will_do: list            # what executing it will change (shown on the form and the request)
    form: str                # the form class name in forms.py
    title: str               # str.format(**cleaned_data)


CATALOG = [
    Workflow("add-vlan", "Add a VLAN to the fabric",
             "A new L2 segment with an anycast gateway, in one of the tier VRFs, on the leaves (and the borders if asked).",
             ["Nautobot: the VLAN, its VNI and gateway in the evpn-3tier plan",
              "evpn-3tier-switches pipeline: VLAN-to-VNI, the anycast gateway and the VLAN on the leaves / borders",
              "phpIPAM: the subnet and its gateway reservation"],
             "AddVlanForm", "Add VLAN {vlan_id} ({name}) to VRF {vrf}"),
    Workflow("lb-peering", "Set up a load balancer to peer with the fabric",
             "A load balancer on a switch pair announcing its VIPs to the fabric over eBGP (BFD optional), in a VRF.",
             ["Nautobot: the load balancer, its peering VLAN, ASN and allowed VIP prefixes",
              "evpn-3tier-switches pipeline: the peering VLAN and BGP neighbours (prefix-filtered) on the switch pair",
              "evpn-3tier-firewalls pipeline: the VIPs in the policy, when outside should reach them",
              "phpIPAM: the peering subnet and the VIP reservations"],
             "LbPeeringForm", "Peer load balancer {name} (AS {asn}) with the {attach_to}"),
]
BY_SLUG = {w.slug: w for w in CATALOG}
