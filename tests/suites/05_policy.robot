*** Settings ***
Documentation     The firewall policy (lab.conf FW_POLICY): from each tier and from outside, a TCP connection to every
...               server and the VIP on every policy port succeeds exactly when the policy (or the shared VRF) allows it.
Resource          ../resources/common.resource

*** Test Cases ***
Only the flows FW_POLICY lists cross between VRFs
    Firewall Policy Should Hold
