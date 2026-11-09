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
    Workflow("vlan-span", "Change a VLAN's span",
             "Put a tier VLAN on the border leaves too (where the firewalls and load balancers attach), or take it back to the leaves only. Not away from the borders while something attached to them uses it.",
             ["pre-flight: the VLAN is in the plan; nothing attached to the border leaves uses it if the borders are to go",
              "Nautobot: the VLAN's span in the evpn-3tier plan (and its description)",
              "evpn-3tier-switches pipeline: the VLAN, its VNI and anycast gateway added to or removed from the border leaves (managed deletions), verified",
              "checks: the VLAN and its gateway on exactly the switches of the new span",
              "ping test from the WAN: every web, app and db VM, and the VLAN's gateway",
              "phpIPAM: the gateway's description says where it lives"],
             "VlanSpanForm", "Change VLAN {vlan_id} ({name}) span: {span_from} → {span_to}",
             (("preflight", "Pre-flight checks"), ("nautobot", "Nautobot"), ("switches", "Switches pipeline"), ("checks", "Device checks"),
              ("ping", "Ping test from the WAN"), ("phpipam", "phpIPAM"))),
    Workflow("fw-rule", "Add a custom firewall rule",
             "Let connections start from one zone to an address or subnet in another — by protocol and port — through the firewall pair. Replies always pass; everything not listed stays dropped and logged.",
             ["Nautobot: the rule in the evpn-3tier firewall policy (fw_policy) with its own rule number (500-899): adding or removing a custom rule never renumbers another",
              "evpn-3tier-firewalls pipeline: the rule on both firewalls, verified",
              "checks: the rule on fw1 and fw2",
              "traffic test: when a lab VM sits at the destination, a connection (TCP) or a ping (ICMP) from the source zone must get through"],
             "FwRuleForm", "Allow {source} → {target} {dest} {proto} {port_label}",
             (("nautobot", "Nautobot"), ("firewalls", "Firewalls pipeline"), ("checks", "Firewall checks"), ("test", "Traffic test"))),
    Workflow("fw-rule-remove", "Remove a custom firewall rule",
             "Take away a custom rule added through Fabric Factory. The lab's own rules and the Standard Firewall Rules (they come and go with their VLAN) are not removable here.",
             ["Nautobot: the rule out of the firewall policy",
              "evpn-3tier-firewalls pipeline: the rule deleted from both firewalls (managed deletions), verified",
              "checks: the rule is gone from fw1 and fw2"],
             "FwRuleRemoveForm", "Remove firewall rule {rule}: {source} → {target} {dest} {proto} {port_label}",
             (("nautobot", "Nautobot"), ("firewalls", "Firewalls pipeline"), ("checks", "Firewall checks"))),
    Workflow("move-server", "Move a server to another VLAN",
             "Move a web or app server to another VLAN of its VRF. It keeps its host number in the new subnet. Its tier keeps serving: the other server carries the traffic during the move (a few minutes).",
             ["pre-flight: the server is where the request says, the new address is free in Nautobot and phpIPAM",
              "phpIPAM: the new address reserved for the server",
              "Nautobot, then evpn-3tier-switches: the new VLAN added to the server's bond, so its leaves carry both VLANs and it is never cut off",
              "firewall rules follow the server: every custom rule that lets traffic reach its old address gets a twin for its new one (evpn-3tier-firewalls)",
              "Nautobot: the address moves to the new VLAN; evpn-3tier-servers moves the server (and re-renders nginx and pg_hba); for a web server evpn-3tier-loadbalancers points HAProxy at it",
              "Nautobot, then evpn-3tier-switches: the old VLAN off the bond and its leaves; the rules for the old address removed; the whole lab verified",
              "checks: the server at its new address, its leaves carrying only the new VLAN; ping test from the WAN",
              "phpIPAM, last: the old address released"],
             "MoveServerForm", "Move {server} from VLAN {from_vlan} to VLAN {to_vlan} ({to_address})",
             (("preflight", "Pre-flight checks"), ("phpipam", "phpIPAM reservation"), ("both", "Both VLANs on its leaves"), ("rules", "Firewall rules follow"),
              ("servers", "Servers pipeline: the move"), ("loadbalancers", "Load balancers pipeline"), ("cleanup", "Old VLAN off, verify"),
              ("checks", "Checks and ping test"), ("release", "phpIPAM release"))),
    Workflow("port", "Port Provisioning",
             "Enable or disable a leaf port, and set a spare port up as an access port (one VLAN, untagged) or a trunk (the VLANs you choose, tagged). Uplinks to the spines are blocked; a server's bond leg can only be enabled or disabled, never in a way that cuts the server off.",
             ["pre-flight: never an uplink to a spine; a server's leg is disabled only while its other leg is up (in Nautobot and live)",
              "Nautobot: the port's admin state, mode (access / tagged), untagged VLAN, tagged VLANs and description — native interface fields",
              "evpn-3tier-switches pipeline: the port shut or brought up; on a spare port its bridge membership (native-vlan or allowed-vlan) — managed deletions take away what no longer applies — then the whole lab verified",
              "checks on the leaf: the port's admin state, and its VLANs on the bridge exactly as asked",
              "ping test from the WAN: every web, app and db VM still answers"],
             "PortForm", "Port {switch} {port}: {after}",
             (("preflight", "Pre-flight checks"), ("nautobot", "Nautobot"), ("switches", "Switches pipeline"), ("checks", "Port checks"),
              ("ping", "Ping test from the WAN"))),
]
BY_SLUG = {w.slug: w for w in CATALOG}
