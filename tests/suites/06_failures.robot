*** Settings ***
Documentation     A component powered off for 20 s under load (an HTTPS read every 100 ms and a write every second from
...               wan's clients, each on a fresh connection), then back. Baseline and after phases must lose nothing;
...               the limits below are for the failure and restore phases and come from measured runs (README).
Resource          ../resources/common.resource
Force Tags        disruptive
Suite Teardown    Application Should Work

*** Test Cases ***
Load balancer lb1 dies: the VIP moves to lb2, nothing is lost
    Run Failure Scenario    lb      max_failed_reads=0    max_failed_writes=0

Web server web1 dies: HAProxy routes around it, nothing is lost
    Run Failure Scenario    web     max_failed_reads=0    max_failed_writes=0

App server app1 dies: nginx routes around it, nothing is lost
    Run Failure Scenario    app     max_failed_reads=0    max_failed_writes=0
