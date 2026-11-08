*** Settings ***
Documentation     The fabric: eBGP underlay and EVPN sessions on every fabric link, BFD, and every Ethernet Segment
...               (servers, load balancers, firewalls) local on both of its switches with the peer as remote VTEP.
Resource          ../resources/common.resource

*** Test Cases ***
Every fabric session, BFD session and Ethernet Segment is up
    Fabric Should Be Healthy
