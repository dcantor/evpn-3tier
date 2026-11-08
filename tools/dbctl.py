#!/usr/bin/env python3
"""PostgreSQL role control for the db tier (db1 / db2): who is primary, promote, re-seed a replica, switch over.

  dbctl.py status                  each db node: up / down, primary / standby, what it streams from or to
  dbctl.py promote NODE            promote a standby (pg_ctl promote): the app follows on its next connection
  dbctl.py reseed NODE FROM        make NODE a fresh streaming replica of FROM (pg_basebackup -R)
  dbctl.py switchover NODE         planned: stop the current primary cleanly (its WAL reaches the standbys), promote
                                   NODE, re-seed the old primary as NODE's replica
The app needs nothing: its connection string lists every db node with target_session_attrs=read-write."""
import sys, time
from labssh import inventory, run

inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}
DBS = sorted((n for n in inv["nodes"] if n["role"] == "db"), key=lambda n: n["idx"])
PGDATA = "/var/lib/postgresql/17/data"


def ip(n): return n["net"]["address"].split("/")[0]


def pg(node, sql, timeout=30):
    rc, out = run(node, f"doas su postgres -s /bin/sh -c \"psql -qtAX -c \\\"{sql}\\\"\" 2>&1", timeout=timeout, inv=inv)
    return rc, out.strip()


def sh(node, cmd, timeout=300):
    rc, out = run(node, f"doas sh -c '{cmd}' 2>&1", timeout=timeout, inv=inv)
    if rc: raise RuntimeError(f"{node}: {cmd[:90]}: rc={rc}: {out[-500:]}")
    return out


def role(node):
    """'primary' | 'standby' | 'down' (PostgreSQL not answering, or the VM unreachable)."""
    try:
        rc, out = pg(node, "select pg_is_in_recovery()", timeout=15)
    except Exception:                                                    # noqa: BLE001
        return "down"
    return {"f": "primary", "t": "standby"}.get(out, "down") if rc == 0 else "down"


def status():
    rows = []
    for d in DBS:
        r = role(d["name"]); extra = ""
        if r == "primary":
            _, o = pg(d["name"], "select string_agg(host(client_addr) || ' ' || state, ', ') from pg_stat_replication")
            extra = f"replicas: {o or 'none'}"
        elif r == "standby":
            _, o = pg(d["name"], "select sender_host || ' ' || status from pg_stat_wal_receiver")
            extra = f"from: {o or 'not streaming'}"
        rows.append((d["name"], r, extra))
    return rows


def primary():
    return next((n for n, r, _ in status() if r == "primary"), None)


def promote(node):
    sh(node, f"su postgres -s /bin/sh -c \"pg_ctl promote -w -D {PGDATA}\"")
    for _ in range(30):
        if role(node) == "primary": return
        time.sleep(1)
    raise RuntimeError(f"{node} did not become primary")


def reseed(node, src):
    """NODE becomes a fresh replica of SRC; PostgreSQL enabled at boot again."""
    sh(node, f"rc-service postgresql stop >/dev/null 2>&1; rm -rf {PGDATA}; mkdir -p /var/lib/postgresql/17 && chown -R postgres:postgres /var/lib/postgresql && "
             f"su postgres -s /bin/sh -c \"PGPASSWORD=replicator pg_basebackup -h {ip(N[src])} -U replicator -D {PGDATA} -R -X stream -c fast\"", timeout=600)
    # the conf.d include and pg_hba come with the copy (they live in the data directory)
    sh(node, "chown -R postgres:postgres /var/lib/postgresql; rc-update add postgresql default >/dev/null 2>&1; rc-service postgresql start >/dev/null")
    for _ in range(30):
        if role(node) == "standby": return
        time.sleep(1)
    raise RuntimeError(f"{node} is not streaming from {src}")


def switchover(node):
    old = primary()
    if old == node: return f"{node} is already the primary"
    if old:
        sh(old, "rc-service postgresql stop >/dev/null")                 # clean: its last WAL reaches the standbys first
    promote(node)
    if old: reseed(old, node)
    for d in DBS:                                                         # any other replica follows the new primary
        if d["name"] not in (node, old) and role(d["name"]) == "standby": reseed(d["name"], node)
    return f"{node} is the primary{', ' + old + ' its replica' if old else ''}"


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "status":
        for n, r, x in status(): print(f"{n:5} {r:8} {x}")
    elif a[0] == "promote" and len(a) == 2: promote(a[1]); print(f"{a[1]} promoted")
    elif a[0] == "reseed" and len(a) == 3: reseed(a[1], a[2]); print(f"{a[1]} re-seeded from {a[2]}")
    elif a[0] == "switchover" and len(a) == 2: print(switchover(a[1]))
    else: sys.exit(__doc__)
