*** Settings ***
Documentation     One long-lived TCP connection web1 -> app1:8080 (a message every 100 ms) through fw1 while fw1 is
...               powered off and back: conntrack-sync has the state on fw2, so the connection survives both moves.
Resource          ../resources/common.resource
Force Tags        disruptive

*** Test Cases ***
A live connection survives fw1 failing and coming back
    Firewall Failover Should Keep The Connection    max_gap=3.0
