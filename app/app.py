#!/usr/bin/env python3
"""evpn-3tier app tier: a small order desk API on :8080 (stdlib HTTP server + psycopg 3).

The web tier (nginx on web1 / web2) proxies /api/ here across the firewall. Every request opens its own database
connection with libpq's multi-host list and target_session_attrs=read-write, so the app always lands on the PostgreSQL
primary — db1 normally, db2 once it is promoted — without a load balancer or VIP in front of the database.

  GET  /health          200 when the database answers
  GET  /api/info        who served this request: lb, web, app, db (and whether it is the primary), the client
  GET  /api/orders      the last 20 orders
  POST /api/orders      {"item": "...", "qty": n} -> the new order (written on the primary)"""
import json, os, socket, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import psycopg

DB = os.environ.get("E3T_DB", "host=172.16.30.11,172.16.30.12 port=5432 dbname=shop user=app password=app "
                                 "target_session_attrs=read-write connect_timeout=2")
ME = socket.gethostname()
SCHEMA = """CREATE TABLE IF NOT EXISTS orders (id bigserial PRIMARY KEY, item text NOT NULL, qty int NOT NULL CHECK (qty > 0),
            created timestamptz NOT NULL DEFAULT now(), web text, app text, db text)"""


def db():
    return psycopg.connect(DB, autocommit=True)


class H(BaseHTTPRequestHandler):
    server_version = "e3t-app"

    def log_message(self, fmt, *args):                              # one line per request, to the service log
        print(f"{self.headers.get('X-Forwarded-For', self.client_address[0])} {fmt % args}", flush=True)

    def reply(self, code, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("X-App-Node", ME)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def chain(self):
        return {"lb": self.headers.get("X-LB-Node"), "web": self.headers.get("X-Web-Node"), "app": ME,
                "client": (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()}

    def do_GET(self):
        t0 = time.time()
        try:
            if self.path == "/health":
                with db() as c: c.execute("SELECT 1")
                return self.reply(200, {"app": ME, "ok": True})
            if self.path == "/api/info":
                with db() as c:
                    addr, standby, n = c.execute("SELECT host(inet_server_addr()), pg_is_in_recovery(), (SELECT count(*) FROM orders)").fetchone()
                return self.reply(200, {**self.chain(), "db": addr, "db_role": "standby" if standby else "primary",
                                        "orders": n, "ms": round((time.time() - t0) * 1000, 1)})
            if self.path == "/api/orders":
                with db() as c:
                    rows = c.execute("SELECT id, item, qty, created, web, app, db FROM orders ORDER BY id DESC LIMIT 20").fetchall()
                cols = ("id", "item", "qty", "created", "web", "app", "db")
                return self.reply(200, [dict(zip(cols, r)) for r in rows])
            self.reply(404, {"error": "not found"})
        except psycopg.Error as e:
            self.reply(503, {**self.chain(), "error": f"database: {e.__class__.__name__}: {str(e).strip()[:200]}"})

    def do_POST(self):
        if self.path != "/api/orders": return self.reply(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            item, qty = str(body.get("item", "")).strip()[:80], int(body.get("qty", 1))
            if not item or qty < 1: return self.reply(400, {"error": "item and a positive qty, please"})
            ch = self.chain()
            with db() as c:
                row = c.execute("INSERT INTO orders (item, qty, web, app, db) VALUES (%s, %s, %s, %s, host(inet_server_addr())) "
                                "RETURNING id, item, qty, created, web, app, db", (item, qty, ch["web"], ME)).fetchone()
            self.reply(201, dict(zip(("id", "item", "qty", "created", "web", "app", "db"), row)))
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "JSON body: {\"item\": \"...\", \"qty\": n}"})
        except psycopg.Error as e:
            self.reply(503, {**self.chain(), "error": f"database: {e.__class__.__name__}: {str(e).strip()[:200]}"})


if __name__ == "__main__":
    for _ in range(30):                                             # the schema, once the primary answers
        try:
            with db() as c: c.execute(SCHEMA)
            break
        except psycopg.Error as e:
            print(f"waiting for the database: {e}", flush=True); time.sleep(2)
    ThreadingHTTPServer.daemon_threads = True
    print(f"{ME}: listening on :8080, database {DB.split(' password')[0]}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
