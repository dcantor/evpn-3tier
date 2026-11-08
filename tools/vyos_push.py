#!/usr/bin/env python3
"""Apply a file of VyOS `set` lines over SSH (netmiko): configure, set*, commit, save. Idempotent — VyOS only commits
the difference. `save` is sent only once the commit has returned to the prompt, and the result is checked with
`compare saved`: typed ahead during a long commit (the leaves' commit hooks run), it could be swallowed, leaving the
change live but unsaved — lost at the next boot.   vyos_push.py HOST FILE"""
import os, re, sys
from netmiko import ConnectHandler

host, path = sys.argv[1], sys.argv[2]
lines = [l.strip() for l in open(path) if l.strip() and not l.startswith("#")]
c = ConnectHandler(device_type="vyos", host=host, username=os.environ.get("VYOS_USERNAME", "vyos"), password=os.environ.get("VYOS_PASSWORD", "vyos"))
try:
    out = c.send_config_set(lines, exit_config_mode=False, cmd_verify=False, read_timeout=240)
    out += c.send_command("commit", expect_string=r"#\s*$", read_timeout=300)                 # wait for the prompt
    out += c.send_command("save", expect_string=r"#\s*$", read_timeout=120)
    unsaved = c.send_command("compare saved", expect_string=r"#\s*$", read_timeout=60)
    c.exit_config_mode()
finally:
    c.disconnect()
bad = [l for l in out.splitlines() if re.search(r"Invalid|Commit failed|is not valid|Error", l)]
if bad: print("FAILED:", *bad, sep="\n  "); sys.exit(1)
if "No changes between" not in unsaved and unsaved.strip():
    print("FAILED: the running configuration is not saved:\n  " + "\n  ".join(unsaved.strip().splitlines()[:10])); sys.exit(1)
print("no changes" if "No configuration changes to commit" in out else f"applied {len(lines)} lines, committed and saved")
