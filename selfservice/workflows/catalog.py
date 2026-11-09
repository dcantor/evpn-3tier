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
    stages: tuple = ()       # (key, label): what the progress view shows; the executor marks each (progress.Progress.stage)


CATALOG = [
    Workflow("add-vlan", "Add a VLAN to the fabric",
             "A new L2 segment with an anycast gateway in one of the tier VRFs, on the leaves (and the borders if asked). You pick the VRF; the app picks the next unused VLAN ID and a free /24 from phpIPAM.",
             ["phpIPAM (the source of truth for addressing), first: reserve the VLAN, the subnet and its gateway (the first host)",
              "Nautobot, from phpIPAM's reservation: the VLAN, its VNI and gateway in the evpn-3tier plan, the VLAN and its prefix",
              "evpn-3tier-switches pipeline: VLAN-to-VNI, the anycast gateway and the VLAN on the leaves / borders; the subnet added to the borders' WAN export policy (prefix-list FABRIC-SUBNETS, called by route-map TO-WAN)",
              "Standard Firewall Rules (Nautobot): ICMP to the new VLAN from every other zone — outside and the other tiers",
              "evpn-3tier-firewalls pipeline: the subnet into prefix-list PUBLIC — routes to outside — and the Standard Firewall Rules",
              "checks: the VLAN and gateway on every switch in the span; both borders permit the subnet to the WAN; both firewalls export it and carry its ICMP rules; the WAN learns the route",
              "ping test from the WAN: every web, app and db VM, and the new VLAN's gateway"],
             "AddVlanForm", "Add VLAN {vlan_id} to VRF {vrf} ({subnet})",
             (("phpipam", "phpIPAM reservation"), ("nautobot", "Nautobot"), ("rules", "Standard firewall rules"), ("switches", "Switches pipeline"),
              ("firewalls", "Firewalls pipeline"), ("checks", "Device and WAN checks"), ("ping", "Ping test from the WAN"))),
    Workflow("remove-vlan", "Remove a VLAN from the fabric",
             "Take a VLAN off the fabric and remove every trace of it: the switch configuration, Nautobot, phpIPAM. Not the lab's own VLANs, not one with hosts in it.",
             ["pre-flight: the VLAN is removable (not the lab's design, no hosts, not its VRF's last) and phpIPAM holds nothing in its subnet but the gateway — else nothing changes",
              "Nautobot: the VLAN out of the evpn-3tier plan, its prefix and the VLAN object deleted, its Standard Firewall Rules removed",
              "evpn-3tier-switches pipeline: what is no longer rendered is deleted — VLAN-to-VNI, the anycast gateway, the VLAN on the bonds and VXLAN, its EVPN VNI, its rule in the borders' WAN prefix-list",
              "evpn-3tier-firewalls pipeline: its rule deleted from prefix-list PUBLIC, its ICMP rules deleted",
              "checks: no trace of the VLAN or its VNI on any switch; the firewalls no longer export it; the WAN no longer has the route",
              "phpIPAM, last (the addresses are free only once nothing uses them): the subnet, its gateway address and the VLAN released"],
             "RemoveVlanForm", "Remove VLAN {vlan_id} ({name}) from the fabric ({subnet})",
             (("preflight", "Pre-flight checks"), ("nautobot", "Nautobot"), ("switches", "Switches pipeline"), ("firewalls", "Firewalls pipeline"), ("checks", "Device and WAN checks"), ("phpipam", "phpIPAM release"))),
    Workflow("lb-peering", "Set up a load balancer to peer with the fabric",
             "A load balancer on the fabric announcing its VIP prefixes to the switch pair it is dual-homed to, over eBGP (BFD optional), in its VRF.",
             ["Nautobot: the peering in the evpn-3tier plan (ASN, peering VLAN and subnet, VIP prefixes, BFD), its VLAN and prefixes",
              "evpn-3tier-switches pipeline: the peering VLAN on the switch pair and on the load balancer's bond legs, eBGP accepting only the VIPs",
              "evpn-3tier-loadbalancers pipeline: the peering VLAN on the load balancer, the VIPs on it, BIRD peering with both switches",
              "phpIPAM: the peering subnet and its addresses, the VIP prefixes",
              "checks: both sessions up, the VIPs routed in the fabric, a VIP answering HTTP from a server in the VRF"],
             "LbPeeringForm", "Peer load balancer {device} (AS {asn}) with the {attach_to}",
             (("nautobot", "Nautobot"), ("switches", "Switches pipeline"), ("loadbalancers", "Load balancers pipeline"),
              ("phpipam", "phpIPAM"), ("checks", "BGP and VIP checks"))),
]
BY_SLUG = {w.slug: w for w in CATALOG}
