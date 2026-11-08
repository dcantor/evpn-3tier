"""Robot Framework keywords for evpn-3tier: thin wrappers over tools/verify.py, tools/chaos.py, tools/dbctl.py and
tools/fw_failover.py (run.sh puts ../tools on the Python path). The checks themselves live in those tools, so the
suites and `lab.sh verify` / `chaos` can never disagree."""
import json, subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from robot.api import logger
import verify, chaos, dbctl

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _assert(results):
    """results: [(name, ok, detail)] -> log every one, fail with the failing ones."""
    bad = []
    for name, ok, detail in results:
        logger.info(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else '  — ' + str(detail)}")
        if not ok: bad.append(f"{name}: {detail}")
    if bad: raise AssertionError(f"{len(bad)} of {len(results)} checks failed:\n" + "\n".join(bad))
    return len(results)


class E3tLib:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def fabric_should_be_healthy(self):
        """Underlay + EVPN sessions, BFD and Ethernet Segments on every spine, leaf and border."""
        with ThreadPoolExecutor(8) as ex:
            res = [r for rs in ex.map(verify.fabric, verify.by_role("spine", "leaf", "border")) for r in rs]
        return _assert(res)

    def firewalls_should_be_healthy(self):
        """Every border <-> firewall session in every VRF; fw1 preferred; fw1 the conntrack-sync VRRP master."""
        return _assert(verify.firewalls())

    def data_plane_should_work(self):
        """Every Linux VM reaches its gateway and the other VMs of its VRF."""
        with ThreadPoolExecutor(8) as ex:
            return _assert([r for rs in ex.map(verify.data, verify.linux()) for r in rs])

    def application_should_work(self):
        """The application from wan's clients through the VIP: every web and app server, the primary, replication."""
        return _assert(verify.app())

    def firewall_policy_should_hold(self):
        """Every flow between tiers (and from outside) open exactly when FW_POLICY allows it."""
        return _assert(verify.policy())

    def run_failure_scenario(self, name, max_failed_reads=0, max_failed_writes=0, max_read_gap=1.0, max_write_gap=2.0, down=20):
        """chaos.py SCENARIO: fail a component under load and restore it. Fails when the failure, restore or after
        phase lost more requests, or went longer without a successful one, than allowed; the baseline and after
        phases must lose nothing."""
        res = chaos.scenario(name, down=int(down), quiet=True)
        logger.info(json.dumps(res, indent=1))
        bad = []
        for p in res["phases"]:
            r, w = p["read"], p["write"]
            logger.info(f"{p['phase']:8} reads {r['requests']} failed {r['failed']} gap {r['longest_gap_s']} s; "
                        f"writes {w['requests']} failed {w['failed']} gap {w['longest_gap_s']} s; {p['served_by']}")
            strict = p["phase"] in ("baseline", "after")
            lim = (0, 0, 1.0, 2.0) if strict else (int(max_failed_reads), int(max_failed_writes), float(max_read_gap), float(max_write_gap))
            if r["failed"] > lim[0]: bad.append(f"{p['phase']}: {r['failed']} reads failed (limit {lim[0]})")
            if w["failed"] > lim[1]: bad.append(f"{p['phase']}: {w['failed']} writes failed (limit {lim[1]})")
            if r["longest_gap_s"] > lim[2]: bad.append(f"{p['phase']}: {r['longest_gap_s']} s without a read (limit {lim[2]} s)")
            if w["longest_gap_s"] > lim[3]: bad.append(f"{p['phase']}: {w['longest_gap_s']} s without a write (limit {lim[3]} s)")
        if bad: raise AssertionError(f"{name}: " + "; ".join(bad))
        return res

    def database_roles_should_be(self, primary):
        rows = dbctl.status(); logger.info(str(rows))
        bad = [f"{n} is {r}" for n, r, _ in rows if r != ("primary" if n == primary else "standby")]
        if bad: raise AssertionError(f"expected {primary} primary, the rest streaming standbys: {', '.join(bad)}")

    def firewall_failover_should_keep_the_connection(self, max_gap=3.0):
        """tools/fw_failover.py: one TCP connection through fw1 while fw1 is powered off and back."""
        r = subprocess.run([str(TOOLS.parent / "tests/.venv/bin/python"), str(TOOLS / "fw_failover.py")], capture_output=True, text=True, timeout=900)
        logger.info(r.stdout + r.stderr)
        if r.returncode: raise AssertionError(f"the connection did not survive:\n{r.stdout[-600:]}")
        gaps = [float(l.split("gap ")[1].split(" s")[0]) for l in r.stdout.splitlines() if "longest gap" in l]
        if any(g > float(max_gap) for g in gaps): raise AssertionError(f"gaps {gaps} s, limit {max_gap} s")
