*** Settings ***
Documentation     The firewalls join the VRFs: every border peers with both in every VRF, every tier VRF's default
...               route points at fw1 (fw2 prepends), and fw1 is the VRRP master that conntrack-sync follows.
Resource          ../resources/common.resource

*** Test Cases ***
Both borders peer with both firewalls in every VRF and prefer fw1
    Firewalls Should Be Healthy
