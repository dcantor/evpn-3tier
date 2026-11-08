*** Settings ***
Documentation     The application from the outside: wan's test clients through the web VIP (HAProxy on lb1), round
...               robin over the web tier, nginx over the app tier, the app on the PostgreSQL primary; writes replicate.
Resource          ../resources/common.resource

*** Test Cases ***
The application answers from outside and uses every server
    Application Should Work

The database runs db1 primary and db2 streaming from it
    Database Roles Should Be    db1
