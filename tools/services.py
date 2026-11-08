#!/usr/bin/env python3
"""Deploy the three-tier application onto the Linux VMs (lab.sh services [node...]). Idempotent; order matters only on
a first deploy (db primary, db replica, app, web, lb), which is the default order.

  db    PostgreSQL 17. db1 (the first db node): initdb, database `shop`, user `app` (from the app VRF only) and
        `replicator` (from the db VRF only). The others: a streaming replica of db1 (pg_basebackup -R), hot standby.
        An existing data directory is kept: a replica is re-seeded only with --reseed.
  app   app/app.py as the OpenRC service e3t-app (supervise-daemon, respawns), :8080
  web   nginx: the page (app/index.html), /health, and /api/ proxied to every app server (no load balancer: nginx
        tries the next one on error / timeout and rests a failed one for 10 s), X-Web-Node on every response
  lb    HAProxy: :443 (TLS, a self-signed lab certificate made once in nodes/.lb-cert/), :80 -> https, round robin over
        the web servers with an HTTP health check every second; stats on the OOB address :8404. keepalived: the web
        VIP (VRRP, unicast between the two, lb1 priority 150, preempting), tracking HAProxy

  services.py [--reseed] [node...]"""
import ipaddress, subprocess, sys
from pathlib import Path
import paramiko
from labssh import LAB, inventory, run

inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}
ROLE_ORDER = ["db", "app", "web", "lb"]


def ip(n): return n["net"]["address"].split("/")[0]
def nodes(role): return sorted((n for n in inv["nodes"] if n["role"] == role), key=lambda n: n["idx"])
def vlan_net(role): return str(ipaddress.ip_interface(nodes(role)[0]["net"]["address"]).network)


def put(node, path, body, mode="644"):
    """Write a file on a Linux VM (through doas tee, the body on stdin)."""
    n = N[node]; c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(n["mgmt_ip"], username="lab", password="lab", look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=30)
    try:
        i, o, e = c.exec_command(f"doas mkdir -p {Path(path).parent} && doas tee {path} >/dev/null && doas chmod {mode} {path}")
        i.write(body); i.channel.shutdown_write()
        if o.channel.recv_exit_status(): raise RuntimeError(f"{node}: writing {path}: {e.read().decode()}")
    finally:
        c.close()


def sh(node, cmd, timeout=180):
    rc, out = run(node, f"doas sh -c {sh_quote(cmd)}", timeout=timeout, inv=inv)
    if rc: raise RuntimeError(f"{node}: {cmd[:80]}...: rc={rc}\n{out[-800:]}")
    return out


def sh_quote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---- db ---------------------------------------------------------------------------------------------------------------
PGDATA = "/var/lib/postgresql/17/data"
PG_CONF = """# evpn-3tier (tools/services.py)
listen_addresses = '*'
wal_level = replica
max_wal_senders = 8
max_replication_slots = 8
wal_keep_size = '256MB'
hot_standby = on
password_encryption = scram-sha-256
shared_buffers = '64MB'
log_connections = off
log_line_prefix = '%m [%p] %u@%d '
"""


def pg_hba():
    return (f"# evpn-3tier (tools/services.py)\nlocal all all trust\n"
            f"host shop app {vlan_net('app')} scram-sha-256\n"
            f"host replication replicator {vlan_net('db')} scram-sha-256\n"
            f"host all all 127.0.0.1/32 trust\n")


def deploy_db(n, reseed=False):
    primary = nodes("db")[0]; me = n["name"]
    has = "yes" in sh(me, f"[ -f {PGDATA}/PG_VERSION ] && echo yes || echo no")
    if n is primary or n["name"] == primary["name"]:
        if not has:
            sh(me, f"mkdir -p {PGDATA} && chown -R postgres:postgres /var/lib/postgresql && "
                   f"su postgres -s /bin/sh -c 'initdb -D {PGDATA} --auth-local=trust --auth-host=scram-sha-256 -E UTF8 >/dev/null'")
        put(me, f"{PGDATA}/conf.d/e3t.conf", PG_CONF); put(me, f"{PGDATA}/pg_hba.conf", pg_hba())
        sh(me, f"grep -q \"^include_dir = 'conf.d'\" {PGDATA}/postgresql.conf || echo \"include_dir = 'conf.d'\" >> {PGDATA}/postgresql.conf; "
               f"chown -R postgres:postgres {PGDATA}; rc-update add postgresql default >/dev/null 2>&1; "
               f"rc-service postgresql status >/dev/null 2>&1 && rc-service postgresql reload >/dev/null || rc-service postgresql start >/dev/null")
        # roles and database through a file: $$ (the DO block) does not survive two shells
        put(me, "/tmp/e3t-init.sql", """DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN CREATE ROLE app LOGIN PASSWORD 'app'; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN CREATE ROLE replicator LOGIN REPLICATION PASSWORD 'replicator'; END IF;
END $$;
SELECT 'CREATE DATABASE shop OWNER app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'shop') \\gexec
""")
        sh(me, "su postgres -s /bin/sh -c 'psql -qtAX -v ON_ERROR_STOP=1 -f /tmp/e3t-init.sql' && rm -f /tmp/e3t-init.sql")
        return f"[{me}] PostgreSQL primary: database shop, users app ({vlan_net('app')}) and replicator ({vlan_net('db')})"
    if has and not reseed:
        put(me, f"{PGDATA}/conf.d/e3t.conf", PG_CONF); put(me, f"{PGDATA}/pg_hba.conf", pg_hba())
        sh(me, "chown -R postgres:postgres /var/lib/postgresql; rc-update add postgresql default >/dev/null 2>&1; "
               "rc-service postgresql status >/dev/null 2>&1 && rc-service postgresql reload >/dev/null || rc-service postgresql start >/dev/null")
        return f"[{me}] PostgreSQL replica kept (its data directory exists; --reseed to copy it again from {primary['name']})"
    sh(me, f"rc-service postgresql stop >/dev/null 2>&1; rm -rf {PGDATA}; mkdir -p {Path(PGDATA).parent} && chown -R postgres:postgres /var/lib/postgresql && "
           f"su postgres -s /bin/sh -c 'PGPASSWORD=replicator pg_basebackup -h {ip(primary)} -U replicator -D {PGDATA} -R -X stream -c fast'", timeout=600)
    put(me, f"{PGDATA}/conf.d/e3t.conf", PG_CONF); put(me, f"{PGDATA}/pg_hba.conf", pg_hba())
    sh(me, "chown -R postgres:postgres /var/lib/postgresql; rc-update add postgresql default >/dev/null 2>&1; rc-service postgresql start >/dev/null")
    return f"[{me}] PostgreSQL replica of {primary['name']} (streaming, hot standby)"


# ---- app --------------------------------------------------------------------------------------------------------------
APP_INIT = """#!/sbin/openrc-run
# evpn-3tier (tools/services.py): the app tier, app/app.py on :8080
name="e3t-app"
command="/usr/bin/python3"
command_args="-u /opt/e3t/app.py"
command_user="nobody:nobody"
supervisor="supervise-daemon"
respawn_delay=2
respawn_max=0
output_log="/var/log/e3t-app.log"
error_log="/var/log/e3t-app.log"
export E3T_DB="@DB@"
depend() { need net; }
start_pre() { checkpath -f -o nobody:nobody -m 644 /var/log/e3t-app.log; }
"""


def deploy_app(n):
    dbs = ",".join(ip(d) for d in nodes("db"))
    conn = f"host={dbs} port=5432 dbname=shop user=app password=app target_session_attrs=read-write connect_timeout=2"
    put(n["name"], "/opt/e3t/app.py", (LAB / "app" / "app.py").read_text(), "755")
    put(n["name"], "/etc/init.d/e3t-app", APP_INIT.replace("@DB@", conn), "755")
    sh(n["name"], "rc-update add e3t-app default >/dev/null 2>&1; rc-service e3t-app restart >/dev/null 2>&1 || rc-service e3t-app start >/dev/null")
    return f"[{n['name']}] app on :8080 (database {dbs}, read-write)"


# ---- web --------------------------------------------------------------------------------------------------------------
def nginx_conf():
    ups = "\n".join(f"    server {ip(a)}:8080 max_fails=1 fail_timeout=10s;" for a in nodes("app"))
    return f"""# evpn-3tier (tools/services.py): the web tier
upstream app {{
{ups}
    keepalive 8;
}}
server {{
    listen 80 default_server;
    root /var/www/e3t;
    add_header X-Web-Node $hostname always;
    location = /health {{ default_type text/plain; return 200 "$hostname ok\\n"; }}
    location /api/ {{
        proxy_pass http://app;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header X-Web-Node $hostname;
        proxy_connect_timeout 2s;
        proxy_read_timeout 6s;
        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 2;
    }}
    location / {{ try_files $uri /index.html; }}
}}
"""


def deploy_web(n):
    put(n["name"], "/var/www/e3t/index.html", (LAB / "app" / "index.html").read_text())
    put(n["name"], "/etc/nginx/http.d/default.conf", nginx_conf())
    sh(n["name"], "mkdir -p /run/nginx && nginx -t -q && { rc-update add nginx default >/dev/null 2>&1; rc-service nginx status >/dev/null 2>&1 && rc-service nginx reload >/dev/null || rc-service nginx start >/dev/null; }")
    return f"[{n['name']}] nginx: the page, /health, /api/ -> {', '.join(a['name'] for a in nodes('app'))}"


# ---- lb ---------------------------------------------------------------------------------------------------------------
def cert():
    d = LAB / "nodes" / ".lb-cert"; pem = d / "web.pem"
    if not pem.exists():
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-subj", "/CN=shop.evpn-3tier.lab",
                        "-addext", f"subjectAltName=DNS:shop.evpn-3tier.lab,IP:{inv['web_vip']}",
                        "-keyout", str(d / "web.key"), "-out", str(d / "web.crt")], check=True, capture_output=True)
        pem.write_text((d / "web.crt").read_text() + (d / "web.key").read_text()); pem.chmod(0o600)
    return pem.read_text()


def haproxy_cfg(n):
    srv = "\n".join(f"    server {w['name']} {ip(w)}:80 check inter 1s fall 2 rise 2" for w in nodes("web"))
    return f"""# evpn-3tier (tools/services.py): the load balancer in front of the web tier
global
    log stdout format raw local0 info
    maxconn 4096
defaults
    mode http
    log global
    option httplog
    option forwardfor
    timeout connect 2s
    timeout client 30s
    timeout server 30s
    retries 2
    option redispatch
frontend http
    bind :80
    http-request return status 200 content-type text/plain string "{n['name']} ok" if {{ path /health }}
    http-request redirect scheme https code 301
frontend https
    bind :443 ssl crt /etc/haproxy/web.pem alpn h2,http/1.1
    http-request set-header X-LB-Node {n['name']}
    http-response set-header X-LB-Node {n['name']}
    default_backend web
backend web
    balance roundrobin
    option httpchk GET /health
    http-check expect status 200
{srv}
listen stats
    bind {n['mgmt_ip']}:8404
    stats enable
    stats uri /
    stats refresh 5s
"""


def keepalived_conf(n):
    lbs = nodes("lb"); first = n["name"] == lbs[0]["name"]; vif = f"bond0.{n['net']['vlan']}"
    peers = "\n".join(f"        {ip(o)}" for o in lbs if o["name"] != n["name"])
    plen = n["net"]["address"].split("/")[1]
    return f"""# evpn-3tier (tools/services.py): the web VIP, VRRP between the load balancers (unicast)
global_defs {{
    router_id {n['name']}
    enable_script_security
    script_user root
}}
vrrp_script haproxy {{
    script "/bin/pidof haproxy"         # busybox pgrep -x never matches haproxy
    interval 1
    fall 2
    rise 2
}}
vrrp_instance WEB {{
    state BACKUP
    interface {vif}
    virtual_router_id 10
    priority {150 if first else 100}
    advert_int 1
    unicast_src_ip {ip(n)}
    unicast_peer {{
{peers}
    }}
    virtual_ipaddress {{
        {inv['web_vip']}/{plen} dev {vif}
    }}
    garp_master_refresh 10
    track_script {{
        haproxy
    }}
}}
"""


def deploy_lb(n):
    put(n["name"], "/etc/haproxy/web.pem", cert(), "600")
    put(n["name"], "/etc/haproxy/haproxy.cfg", haproxy_cfg(n))
    put(n["name"], "/etc/keepalived/keepalived.conf", keepalived_conf(n))
    sh(n["name"], "haproxy -c -q -f /etc/haproxy/haproxy.cfg && for s in haproxy keepalived; do rc-update add $s default >/dev/null 2>&1; "
                  "rc-service $s status >/dev/null 2>&1 && rc-service $s reload >/dev/null || rc-service $s start >/dev/null; done")
    return f"[{n['name']}] HAProxy :443 / :80 -> {', '.join(w['name'] for w in nodes('web'))}; keepalived VIP {inv['web_vip']}"


DEPLOY = {"db": deploy_db, "app": deploy_app, "web": deploy_web, "lb": deploy_lb}

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]; reseed = "--reseed" in sys.argv
    todo = [N[a] for a in args] if args else [n for r in ROLE_ORDER for n in nodes(r)]
    todo.sort(key=lambda n: (ROLE_ORDER.index(n["role"]), n["idx"]))
    rc = 0
    for n in todo:
        try:
            print(DEPLOY[n["role"]](n, reseed) if n["role"] == "db" else DEPLOY[n["role"]](n))
        except Exception as e:                                                # noqa: BLE001
            print(f"[{n['name']}] FAILED: {e}"); rc = 1
    sys.exit(rc)
