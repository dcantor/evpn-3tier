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
             "A load balancer on the fabric announcing its VIP prefixes to the switch pair it is dual-homed to, over eBGP (BFD optional), in its VRF.",
             ["Nautobot: the peering in the evpn-3tier plan (ASN, peering VLAN and subnet, VIP prefixes, BFD), its VLAN and prefixes",
              "evpn-3tier-switches pipeline: the peering VLAN on the switch pair and on the load balancer's bond legs, eBGP accepting only the VIPs",
              "evpn-3tier-loadbalancers pipeline: the peering VLAN on the load balancer, the VIPs on it, BIRD peering with both switches",
              "phpIPAM: the peering subnet and its addresses, the VIP prefixes",
              "checks: both sessions up, the VIPs routed in the fabric, a VIP answering HTTP from a server in the VRF"],
             "LbPeeringForm", "Peer load balancer {device} (AS {asn}) with the {attach_to}"),
]
BY_SLUG = {w.slug: w for w in CATALOG}
