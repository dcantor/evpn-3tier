"""The systems a workflow touches: Nautobot (the source of truth), Gitea (the deploy pipelines) and phpIPAM (the external
IPAM). Credentials are read at run time — ~/.config/evpn-3tier/selfservice.env if it has them, else where the lab's other
tools find them (the NMS's /opt/nautobot/.env over SSH, phpIPAM's ~/.config/evpn-3tier/phpipam.env) — never stored in git."""
import os
import subprocess
import time
from pathlib import Path

import requests
from django.conf import settings

NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080")
GITEA_URL = os.environ.get("GITEA_URL", "http://10.0.0.10:3000")
GITEA_WEB = "http://192.168.50.231:3000"                     # what users can open (the LAN relay to Gitea)
LOCATION = "evpn-3tier"
_secret = {}


def _nms(key):
    """A value from the NMS's /opt/nautobot/.env (Nautobot token, Gitea password), as the lab's tools read it."""
    if key in settings.ENV: return settings.ENV[key]
    if key not in _secret:
        out = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                              "-o", "ConnectTimeout=10", "lab@10.0.0.10", f"grep ^{key}= /opt/nautobot/.env | cut -d= -f2"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if not out: raise RuntimeError(f"{key}: not in selfservice.env and not readable on the NMS")
        _secret[key] = out
    return _secret[key]


# ---- Nautobot -------------------------------------------------------------------------------------------------------------------
class Nautobot:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token {_nms('NAUTOBOT_SUPERUSER_API_TOKEN')}", "Accept": "application/json",
                               "Content-Type": "application/json"})

    def get(self, path, **params):
        r = self.s.get(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", params=params, timeout=30); r.raise_for_status(); return r.json()

    def one(self, path, **params):
        res = self.get(path, **params)["results"]; return res[0] if res else None

    def post(self, path, data):
        r = self.s.post(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", json=data, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"Nautobot POST {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def patch(self, path, data):
        r = self.s.patch(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", json=data, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"Nautobot PATCH {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def graphql(self, query):
        r = self.s.post(f"{NAUTOBOT_URL}/api/graphql/", json={"query": query}, timeout=60); r.raise_for_status()
        body = r.json()
        if body.get("errors"): raise RuntimeError(f"Nautobot GraphQL: {body['errors']}")
        return body["data"]

    def context(self):
        return self.one("extras/config-contexts", name=LOCATION)


# ---- Gitea (the deploy pipelines) ------------------------------------------------------------------------------------------------
class Gitea:
    def __init__(self):
        self.auth = ("lab", _nms("GITEA_PASSWORD"))

    def runs(self, repo, n=10):
        r = requests.get(f"{GITEA_URL}/api/v1/repos/lab/{repo}/actions/tasks", params={"limit": n}, auth=self.auth, timeout=30); r.raise_for_status()
        return r.json()["workflow_runs"]

    def dispatch_and_wait(self, repo, log, timeout=3600):
        """Start the repo's deploy workflow on main, wait for it; returns (success, run number, url)."""
        before = max((t["run_number"] for t in self.runs(repo, 5)), default=0)
        r = requests.post(f"{GITEA_URL}/api/v1/repos/lab/{repo}/actions/workflows/deploy.yml/dispatches", json={"ref": "main"}, auth=self.auth, timeout=30)
        if r.status_code != 204: raise RuntimeError(f"Gitea dispatch {repo}: {r.status_code} {r.text[:200]}")
        t0, run, announced = time.time(), None, False
        while time.time() - t0 < timeout:
            run = next((t for t in self.runs(repo, 5) if t["run_number"] > before and t["event"] == "workflow_dispatch"), None)
            if run and not announced:
                log(f"{repo} run #{run['run_number']} started: {GITEA_WEB}/lab/{repo}/actions/runs/{run['run_number']}"); announced = True
            if run and run["status"] not in ("running", "waiting", "queued", "blocked"): break
            time.sleep(15)
        else:
            raise RuntimeError(f"{repo}: no result after {timeout} s")
        return run["status"] == "success", run["run_number"], f"{GITEA_WEB}/lab/{repo}/actions/runs/{run['run_number']}"


# ---- phpIPAM ------------------------------------------------------------------------------------------------------------------------
class Ipam:
    def __init__(self):
        env = dict(l.split("=", 1) for l in (Path.home() / ".config/evpn-3tier/phpipam.env").read_text().split() if "=" in l)
        self.api = f"{env['PHPIPAM_URL'].rstrip('/')}/api/{env['API_APP']}"
        self.s = requests.Session(); self.s.verify = False
        self.s.headers.update({"phpipam-token": env["TOKEN"], "Content-Type": "application/json"})
        requests.packages.urllib3.disable_warnings()

    def req(self, method, path, **kw):
        r = self.s.request(method, f"{self.api}/{path.strip('/')}/", timeout=30, **kw)
        body = r.json()
        if not body.get("success") and not (method == "GET" and r.status_code == 404):
            raise RuntimeError(f"phpIPAM {method} {path}: {r.status_code} {body.get('message')}")
        return body.get("data") if body.get("success") else None
