*** Settings ***
Documentation     Every Linux VM reaches its anycast gateway and every other VM of its VRF.
Resource          ../resources/common.resource

*** Test Cases ***
Every VM reaches its gateway and its VRF
    Data Plane Should Work
