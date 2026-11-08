*** Settings ***
Documentation     Network nodes powered off for 20 s under load, then back. Measured (0.4.0): leaf1 0 failed
...               requests, border1 4 failed reads (longest gap 0.3 s), fw1 0 — the limits leave room for BFD timing.
Resource          ../resources/common.resource
Force Tags        disruptive
Suite Teardown    Fabric Should Be Healthy

*** Test Cases ***
Leaf leaf1 dies: its servers keep their other bond leg
    Run Failure Scenario    leaf      max_failed_reads=10    max_failed_writes=2    max_read_gap=3.0    max_write_gap=3.0

Border border1 dies: firewalls, load balancers and wan keep border2
    Run Failure Scenario    border    max_failed_reads=20    max_failed_writes=3    max_read_gap=3.0    max_write_gap=3.0

Firewall fw1 dies: every VRF moves to fw2
    Run Failure Scenario    fw        max_failed_reads=10    max_failed_writes=2    max_read_gap=3.0    max_write_gap=3.0
