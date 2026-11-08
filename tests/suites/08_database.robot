*** Settings ***
Documentation     db1 (the primary) powered off under load; db2 promoted 5 s later (standing in for the operator or an
...               automation); the app finds the new primary on its next connection — no VIP, no load balancer:
...               target_session_attrs=read-write. Restore: db1 re-seeded as db2's replica, then a planned switchover
...               back. Reads need the database too (/api/info asks it), so both pause until the promotion. Measured
...               (0.4.0): 32 failed reads, 3 failed writes, longest gaps 3.3 s / 3.8 s.
Resource          ../resources/common.resource
Force Tags        disruptive
Suite Teardown    Database Roles Should Be    db1

*** Test Cases ***
Primary db1 dies: db2 is promoted and the app follows it
    Run Failure Scenario    db    max_failed_reads=80    max_failed_writes=8    max_read_gap=8.0    max_write_gap=8.0
    Database Roles Should Be    db1
