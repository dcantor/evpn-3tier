"""The self-service app: request forms validated against the lab, requests and their history, who sees and cancels what.
The lab model is a fixed copy of evpn-3tier's (the tests do not need the lab running).   manage.py test"""
import ipaddress
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from . import forms as F
from .models import WorkflowRequest

LAB = {
    "lab": "evpn-3tier", "vrfs": ["web", "app", "db", "outside"], "tier_vrfs": ["web", "app", "db"],
    "vlans_used": [10, 20, 30, 110, 120, 130, 199, 4010, 4020, 4030, 4090],
    "vnis_used": [10010, 10020, 10030, 10110, 10120, 10130, 10199, 50010, 50020, 50030, 50099],
    "subnets_used": [ipaddress.ip_network(n) for n in ("172.16.10.0/24", "172.16.20.0/24", "172.16.30.0/24", "172.16.110.0/28",
                                                       "172.16.120.0/28", "172.16.130.0/28", "172.16.199.0/28")],
    "asns_used": [65000, 65100, 65101, 65102, 65103, 65104, 65121, 65122, 65200, 65500],
    "switch_pairs": {"rack1 leaves": ["leaf1", "leaf2"], "rack2 leaves": ["leaf3", "leaf4"], "border leaves": ["border1", "border2"]},
    "tier_vlans": [],
    "lbs": {"lb1": {"pair": "border leaves", "switches": ["border1", "border2"], "vrf": "web", "peering": None},
            "lb2": {"pair": "border leaves", "switches": ["border1", "border2"], "vrf": "web", "peering": {"asn": 65320}}},
    "lb_peerings": [],
    "vlans": [{"vlan": 10, "vrf": "web", "vni": 10010, "gateway": "172.16.10.1/24", "subnet": "172.16.10.0/24", "on": ["leaves", "borders"]},
              {"vlan": 20, "vrf": "app", "vni": 10020, "gateway": "172.16.20.1/24", "subnet": "172.16.20.0/24", "on": ["leaves"]},
              {"vlan": 30, "vrf": "db", "vni": 10030, "gateway": "172.16.30.1/24", "subnet": "172.16.30.0/24", "on": ["leaves"]}],
    "hosts": [{"name": "lb1", "address": "172.16.10.5/24"}, {"name": "web1", "address": "172.16.10.11/24"}],
    "web_vip": "172.16.10.10",
}
patched = mock.patch("workflows.labmodel.model", return_value=LAB)

VLAN_OK = {"vlan_id": 40, "name": "app-backup", "vrf": "app", "gateway": "172.16.40.1/24", "span": "leaves", "reason": "backups"}
LB_OK = {"action": "add", "tier": "app", "reason": "a load balancer for the app tier"}
# lbs.py reads the hardware inventory (added-lbs.conf), phpIPAM and the host's memory: fixed here
lb_env = [mock.patch("workflows.lbs.added", return_value=[]), mock.patch("workflows.lbs.ipam_used", return_value=set()),
          mock.patch("workflows.lbs.mem_available_mib", return_value=8000)]


def with_lb_env(cls):
    for m in lb_env: cls = m(cls)
    return cls


@patched
class AddVlanFormTests(TestCase):
    def test_valid(self, _m):
        f = F.AddVlanForm(VLAN_OK); self.assertTrue(f.is_valid(), f.errors)
        p = f.params(); self.assertEqual((p["vni"], p["subnet"]), (10040, "172.16.40.0/24"))

    def test_vlan_in_use(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "vlan_id": 110}); self.assertFalse(f.is_valid()); self.assertIn("vlan_id", f.errors)

    def test_subnet_overlaps(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "gateway": "172.16.10.129/25"}); self.assertFalse(f.is_valid()); self.assertIn("overlaps", str(f.errors["gateway"]))

    def test_outside_fabric_space(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "gateway": "10.99.0.1/24"}); self.assertFalse(f.is_valid()); self.assertIn("gateway", f.errors)

    def test_gateway_must_be_a_host(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "gateway": "172.16.40.0/24"}); self.assertFalse(f.is_valid())

    def test_name_is_computed(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "name": "ignored"}); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.params()["name"], "vlan_40_app")

    def test_gateway_is_first_host(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "gateway": "172.16.40.254/24"}); self.assertFalse(f.is_valid())
        self.assertIn("first host", str(f.errors["gateway"]))

    def test_open_request_holds_its_vlan_and_subnet(self, _m):
        u = User.objects.create_user("h", password="h-password-123")
        WorkflowRequest.objects.create(workflow="add-vlan", title="t", requested_by=u, status="submitted",
                                       parameters={"vlan_id": 40, "subnet": "172.16.40.0/24"})
        f = F.AddVlanForm(VLAN_OK); self.assertFalse(f.is_valid())
        self.assertIn("open request", str(f.errors["vlan_id"])); self.assertIn("request #", str(f.errors["gateway"]))

    def test_allocation(self, _m):
        import ipaddress as I
        from . import allocate
        u = User.objects.create_user("h", password="h-password-123")
        WorkflowRequest.objects.create(workflow="add-vlan", title="t", requested_by=u, status="approved",
                                       parameters={"vlan_id": 11, "subnet": "172.16.0.0/24"})
        lab = {**LAB, "tier_vlans": [{"vlan": 10, "vrf": "web", "gateway": "172.16.10.1/24", "on": "leaves"}]}
        with mock.patch("workflows.labmodel.model", return_value=lab), \
             mock.patch.object(allocate, "ipam_vlans", return_value={12}), \
             mock.patch.object(allocate, "free_subnets", return_value=[I.ip_network("172.16.0.0/24"), I.ip_network("172.16.1.0/24")]):
            a = allocate.add_vlan("db")
        # 11 is held by the open request, 12 is in phpIPAM; 172.16.0.0/24 is held too
        self.assertEqual((a["vlan_id"], a["vni"], a["name"], a["subnet"], a["gateway"]), (13, 10013, "vlan_13_db", "172.16.1.0/24", "172.16.1.1/24"))

    def test_outside_vrf_not_offered(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "vrf": "outside"}); self.assertFalse(f.is_valid()); self.assertIn("vrf", f.errors)


@patched
@with_lb_env
class LbPeeringFormTests(TestCase):
    """Set up a load balancer pair: Add (the requester picks only the tier) or Delete (the last pair added)."""
    def test_add_allocates_a_pair(self, *_):
        f = F.LbPeeringForm(LB_OK); self.assertTrue(f.is_valid(), f.errors)
        p = f.params(); a, b = p["members"]
        self.assertEqual((p["op"], p["names"], p["vrid"], p["tier_vlan"], p["span_added"], p["vrrp_address"]), ("add", ["lb3", "lb4"], 103, 20, True, "172.16.20.20/24"))
        self.assertEqual((a["asn"], a["peering_vlan"], a["peering_subnet"], a["address"], a["border_port"], a["es"], a["priority"], a["partner"]),
                         (65330, 170, "172.16.170.0/29", "172.16.20.21/24", "eth8", 15, 150, "lb4"))
        self.assertEqual((b["asn"], b["peering_vlan"], b["peering_subnet"], b["address"], b["border_port"], b["es"], b["priority"], b["partner"]),
                         (65340, 180, "172.16.180.0/29", "172.16.20.22/24", "eth9", 16, 100, "lb3"))
        self.assertIn("active / standby", p["plan_text"]); self.assertIn("extended to the border leaves", p["plan_text"])

    def test_add_web_skips_used_addresses_asns_subnets(self, *_):
        lab = {**LAB, "asns_used": LAB["asns_used"] + [65330], "hosts": LAB["hosts"] + [{"name": "x", "address": "172.16.10.20/24"}],
               "subnets_used": LAB["subnets_used"] + [ipaddress.ip_network("172.16.170.0/28")]}
        with mock.patch("workflows.labmodel.model", return_value=lab):
            f = F.LbPeeringForm({**LB_OK, "tier": "web"}); self.assertTrue(f.is_valid(), f.errors); p = f.params()
        a, b = p["members"]
        self.assertEqual((a["asn"], b["asn"]), (65340, 65350))            # 65330 taken; the two members never share
        self.assertEqual((a["peering_subnet"], b["peering_subnet"]), ("172.16.150.0/29", "172.16.180.0/29"))
        self.assertEqual((p["vrrp_address"], a["address"], b["address"], p["span_added"]), ("172.16.10.21/24", "172.16.10.22/24", "172.16.10.23/24", False))

    def test_add_needs_a_tier(self, *_):
        f = F.LbPeeringForm({**LB_OK, "tier": ""}); self.assertFalse(f.is_valid()); self.assertIn("tier", f.errors)

    def test_add_none_left(self, *_):
        with mock.patch("workflows.lbs.added", return_value=["lb3", "lb4", "lb5", "lb6"]):
            f = F.LbPeeringForm(LB_OK); self.assertFalse(f.is_valid()); self.assertIn("spare ports", str(f.errors))

    def test_second_pair_is_lb5_lb6(self, *_):
        from . import lbs
        with mock.patch("workflows.lbs.added", return_value=["lb3", "lb4"]):
            self.assertEqual(lbs.next_pair(), (5, 6)); self.assertEqual(lbs.last_pair(), ["lb3", "lb4"])

    def test_design_pair_cannot_go(self, *_):
        f = F.LbPeeringForm({**LB_OK, "action": "delete:lb1+lb2"}); self.assertFalse(f.is_valid()); self.assertIn("design", str(f.errors["action"]))

    def test_delete_last_pair_only(self, *_):
        prs = [{"name": n, "vrf": "app", "asn": a, "vlan": v, "subnet": f"172.16.{v}.0/29", "added": True, "tier_vlan": 20, "span_added": True,
                "border_port": f"eth{5 + int(n[2:])}", "ha": {"address": "172.16.20.20/24"}} for n, a, v in (("lb5", 65350, 190), ("lb6", 65360, 200))]
        lbs_ = {**LAB["lbs"], **{n: {"vrf": "web", "peering": {"asn": 1}} for n in ("lb3", "lb4")}, "lb5": {"vrf": "app", "peering": prs[0]}, "lb6": {"vrf": "app", "peering": prs[1]}}
        with mock.patch("workflows.labmodel.model", return_value={**LAB, "lbs": lbs_, "lb_peerings": prs}), \
             mock.patch("workflows.lbs.added", return_value=["lb3", "lb4", "lb5", "lb6"]):
            f = F.LbPeeringForm({**LB_OK, "action": "delete:lb3+lb4"}); self.assertFalse(f.is_valid()); self.assertIn("lb5 + lb6 first", str(f.errors["action"]))
            f = F.LbPeeringForm({**LB_OK, "action": "delete:lb5+lb6", "tier": ""}); self.assertTrue(f.is_valid(), f.errors)
            p = f.params(); self.assertEqual((p["op"], p["names"], [e["vlan"] for e in p["entries"]]), ("delete", ["lb5", "lb6"], [190, 200]))
            self.assertIn("back to the leaves", p["plan_text"]); self.assertIn("172.16.20.20/24", p["plan_text"])

    def test_one_request_at_a_time(self, *_):
        u = User.objects.create_user("h", password="h-password-123")
        WorkflowRequest.objects.create(workflow="lb-peering", title="t", requested_by=u, status="submitted", parameters={"op": "add"})
        f = F.LbPeeringForm(LB_OK); self.assertFalse(f.is_valid()); self.assertIn("one at a time", str(f.errors))

    def test_open_pair_holds_its_vlans(self, *_):
        from .allocate import claims
        u = User.objects.create_user("h", password="h-password-123")
        WorkflowRequest.objects.create(workflow="lb-peering", title="t", requested_by=u, status="approved",
                                       parameters={"op": "add", "members": [{"peering_vlan": 170, "vni": 10170, "peering_subnet": "172.16.170.0/29"},
                                                                            {"peering_vlan": 180, "vni": 10180, "peering_subnet": "172.16.180.0/29"}]})
        v, vni, nets = claims(); self.assertTrue({170, 180} <= v and {10170, 10180} <= vni and len(nets) == 2)

    def test_stages_follow_the_action(self, *_):
        from .catalog import BY_SLUG
        wf = BY_SLUG["lb-peering"]
        self.assertEqual(wf.stages_for({"op": "add"})[3][0], "rack"); self.assertEqual(wf.stages_for({"op": "delete"})[2][0], "switches")


@patched
class RequestFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="alice-password-1")
        self.bob = User.objects.create_user("bob", password="bob-password-1")
        self.op = User.objects.create_user("olga", password="olga-password-1")
        ops = Group.objects.create(name="operators"); ops.permissions.add(Permission.objects.get(codename="view_all_requests")); self.op.groups.add(ops)

    def submit(self, user, slug="add-vlan", data=VLAN_OK):
        self.client.force_login(user)
        return self.client.post(reverse("new_request", args=[slug]), data)

    def test_login_required(self, _m):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_submit_records_request_and_event(self, _m):
        r = self.submit(self.alice); self.assertEqual(r.status_code, 302)
        wr = WorkflowRequest.objects.get()
        self.assertEqual((wr.title, wr.status, wr.requested_by), ("Add VLAN 40 to VRF app (172.16.40.0/24)", "submitted", self.alice))
        self.assertEqual(wr.parameters["vni"], 10040)
        self.assertEqual(list(wr.events.values_list("kind", flat=True)), ["submitted"])

    def test_lb_request(self, _m):
        with lb_env[0], lb_env[1], lb_env[2]: self.submit(self.alice, "lb-peering", LB_OK)
        self.assertEqual(WorkflowRequest.objects.get().title, "Add load balancer pair lb3 + lb4 (active / standby) in VRF app, peering with the border leaves")

    def test_requesters_see_only_their_own(self, _m):
        self.submit(self.alice); wr = WorkflowRequest.objects.get()
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("request_detail", args=[wr.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("all_requests")).status_code, 403)

    def test_operators_see_everything(self, _m):
        self.submit(self.alice); wr = WorkflowRequest.objects.get()
        self.client.force_login(self.op)
        self.assertEqual(self.client.get(reverse("request_detail", args=[wr.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("all_requests")), wr.title)

    def test_cancel_own(self, _m):
        self.submit(self.alice); wr = WorkflowRequest.objects.get()
        self.client.post(reverse("cancel_request", args=[wr.pk]))
        wr.refresh_from_db(); self.assertEqual(wr.status, "cancelled")
        self.assertEqual(list(wr.events.values_list("kind", flat=True)), ["submitted", "cancelled"])

    def test_cannot_cancel_twice(self, _m):
        self.submit(self.alice); wr = WorkflowRequest.objects.get()
        self.client.post(reverse("cancel_request", args=[wr.pk])); self.client.post(reverse("cancel_request", args=[wr.pk]))
        self.assertEqual(wr.events.count(), 2)

    def test_invalid_request_is_not_saved(self, _m):
        r = self.submit(self.alice, data={**VLAN_OK, "vlan_id": 10})
        self.assertEqual(r.status_code, 200); self.assertEqual(WorkflowRequest.objects.count(), 0)

    def test_pages_render(self, _m):
        self.client.force_login(self.op)
        for name, args in (("dashboard", []), ("catalog", []), ("new_request", ["add-vlan"]), ("new_request", ["lb-peering"]), ("all_requests", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)


@patched
class ApprovalTests(TestCase):
    def setUp(self):
        # setUp submits a request: the fixed lab model must apply here too (a class decorator patches only the tests —
        # unpatched, setUp validated against the live lab, where VLAN 40 now exists)
        p = mock.patch("workflows.labmodel.model", return_value=LAB); p.start(); self.addCleanup(p.stop)
        self.alice = User.objects.create_user("alice", password="alice-password-1")
        self.op = User.objects.create_user("olga", password="olga-password-1")
        ops = Group.objects.create(name="operators")
        ops.permissions.add(*Permission.objects.filter(codename__in=["view_all_requests", "approve_requests"])); self.op.groups.add(ops)
        self.client.force_login(self.alice); self.client.post(reverse("new_request", args=["add-vlan"]), VLAN_OK)
        self.wr = WorkflowRequest.objects.get()

    def test_requester_cannot_approve(self, _m):
        self.assertEqual(self.client.post(reverse("decide_request", args=[self.wr.pk, "approve"])).status_code, 403)

    def test_operator_approves_and_worker_runs(self, _m):
        self.client.force_login(self.op)
        self.client.post(reverse("decide_request", args=[self.wr.pk, "approve"]), {"note": "ok"})
        self.wr.refresh_from_db(); self.assertEqual((self.wr.status, self.wr.decided_by), ("approved", self.op))
        from django.core.management import call_command
        with mock.patch.dict("workflows.executors.EXECUTORS", {"add-vlan": lambda r, log: (log("step one"), {"x": 1})[1]}):
            call_command("e3t_worker", once=True)
        self.wr.refresh_from_db(); self.assertEqual(self.wr.status, "done"); self.assertEqual(self.wr.result["x"], 1)
        self.assertEqual(list(self.wr.events.values_list("kind", flat=True)), ["submitted", "approved", "started", "step", "done"])

    def test_failure_then_retry(self, _m):
        self.client.force_login(self.op); self.client.post(reverse("decide_request", args=[self.wr.pk, "approve"]))
        from django.core.management import call_command
        def boom(r, log): raise RuntimeError("pipeline failed")
        with mock.patch.dict("workflows.executors.EXECUTORS", {"add-vlan": boom}): call_command("e3t_worker", once=True)
        self.wr.refresh_from_db(); self.assertEqual(self.wr.status, "failed"); self.assertIn("pipeline failed", self.wr.result["error"])
        self.client.post(reverse("decide_request", args=[self.wr.pk, "retry"]))
        self.wr.refresh_from_db(); self.assertEqual(self.wr.status, "approved")

    def test_reject(self, _m):
        self.client.force_login(self.op); self.client.post(reverse("decide_request", args=[self.wr.pk, "reject"]), {"note": "not now"})
        self.wr.refresh_from_db(); self.assertEqual(self.wr.status, "rejected")
        self.assertEqual(self.client.post(reverse("decide_request", args=[self.wr.pk, "approve"])).status_code, 302)   # no longer submitted
        self.wr.refresh_from_db(); self.assertEqual(self.wr.status, "rejected")

    def test_unknown_decision(self, _m):
        self.client.force_login(self.op); self.assertEqual(self.client.post(reverse("decide_request", args=[self.wr.pk, "bless"])).status_code, 404)


# ---- live progress (0.11.0) -------------------------------------------------------------------------------------------------
class PipelineLogTests(TestCase):
    def test_ansible_and_verify_lines(self):
        from .progress import PipelineLog
        snap = {"steps": [{"name": "4 apply", "status": "running", "duration": "1s"}, {"name": "5 verify", "status": "waiting", "duration": ""}], "tail": []}
        PipelineLog(snap).feed(0, ["PLAY [VyOS configuration] ****", "TASK [Push config] ****", "ok: [leaf1]", "changed: [border1]",
                                   "fatal: [lb1]: FAILED! => {}", "skipping: [leaf2]", "\x1b[0;32mok: [leaf1] => (item=x)\x1b[0m"])
        PipelineLog(snap).feed(1, ["PASS  leaf1: BGP 4/4", "FAIL  border1: BFD 11/12 up  — down: ['x']", "60 of 61 checks passed"])
        h = snap["steps"][0]["hosts"]
        self.assertEqual((snap["play"], snap["task"]), ("VyOS configuration", "Push config"))
        self.assertEqual(h["leaf1"]["ok"], 2); self.assertEqual(h["border1"]["changed"], 1); self.assertEqual(h["lb1"]["failed"], 1); self.assertEqual(h["leaf2"]["skipped"], 1)
        self.assertEqual((snap["verify"]["passed"], snap["verify"]["failed"], snap["verify"]["total"]), (1, 1, 61))
        self.assertIn("border1: BFD", snap["verify"]["failures"][0])
        self.assertTrue(all("\x1b" not in l for l in snap["tail"]))


class ProgressTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("p", password="p-password-123")
        self.r = WorkflowRequest.objects.create(workflow="lb-peering", title="t", requested_by=self.u, status="running")

    def test_stages_pipeline_and_percent(self):
        from .progress import Progress
        from .services import percent
        p = Progress(self.r, (("nautobot", "Nautobot"), ("switches", "Switches"), ("checks", "Checks")))
        p.stage("nautobot"); p("did a thing"); p.stage("switches")
        snap = p.pipeline("evpn-3tier-switches"); snap = p.pipeline("evpn-3tier-switches", run=7, url="u")
        snap["steps"] = [{"name": "a", "status": "success", "duration": "1s"}, {"name": "b", "status": "running", "duration": "1s"}]
        p.save(force=True); self.r.refresh_from_db()
        st = {s["key"]: s["status"] for s in self.r.progress["stages"]}
        self.assertEqual(st, {"nautobot": "done", "switches": "running", "checks": "pending"})
        self.assertEqual(list(self.r.progress["pipelines"]), ["evpn-3tier-switches#7"])          # the placeholder was re-keyed
        self.assertEqual(percent(self.r), round(100 * (1 + 0.5) / 3))
        p.finish(False); self.r.refresh_from_db()
        self.assertEqual(self.r.progress["stages"][1]["status"], "failed")
        # a retry starts the stages again, keeps the earlier pipeline runs
        p2 = Progress(self.r, (("nautobot", "Nautobot"), ("switches", "Switches"), ("checks", "Checks")))
        self.assertEqual(p2.p["attempt"], 2); self.assertIn("evpn-3tier-switches#7", p2.p["pipelines"])
        self.assertTrue(p2.p["stages"][0]["previous"])


@patched
class ApiTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="alice-password-1")
        self.op = User.objects.create_user("olga", password="olga-password-1")
        ops = Group.objects.create(name="operators")
        ops.permissions.add(*Permission.objects.filter(codename__in=["view_all_requests", "approve_requests"])); self.op.groups.add(ops)

    def sign_in(self, user, password):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        tok = c.get("/api/session").json()["csrf"]
        r = c.post("/api/session", {"username": user, "password": password}, content_type="application/json", HTTP_X_CSRFTOKEN=tok)
        return c, r

    def test_sign_in_csrf_and_wrong_password(self, _m):
        from django.test import Client
        c = Client(enforce_csrf_checks=True); c.get("/api/session")
        self.assertEqual(c.post("/api/session", {"username": "alice", "password": "alice-password-1"}, content_type="application/json").status_code, 403)
        _, r = self.sign_in("alice", "nope"); self.assertEqual(r.status_code, 401)
        c, r = self.sign_in("alice", "alice-password-1"); self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["username"], "alice"); self.assertFalse(r.json()["can_approve"])
        self.assertEqual(c.post("/api/requests", {"workflow": "add-vlan", "data": {}}, content_type="application/json").status_code, 403)  # no CSRF header

    def test_create_validate_approve(self, _m):
        c, r = self.sign_in("alice", "alice-password-1"); h = {"HTTP_X_CSRFTOKEN": r.json()["csrf"]}
        bad = c.post("/api/requests", {"workflow": "add-vlan", "data": {**VLAN_OK, "vlan_id": 10}}, content_type="application/json", **h)
        self.assertEqual(bad.status_code, 400); self.assertIn("vlan_id", bad.json()["errors"])
        with lb_env[0], lb_env[1], lb_env[2]:
            ok = c.post("/api/requests", {"workflow": "lb-peering", "data": LB_OK}, content_type="application/json", **h)
        self.assertEqual(ok.status_code, 200, ok.content); rid = ok.json()["id"]
        self.assertEqual(WorkflowRequest.objects.get(pk=rid).parameters["bfd"], True)
        self.assertEqual(c.post(f"/api/requests/{rid}/approve", {"note": ""}, content_type="application/json", **h).status_code, 403)
        self.assertEqual(c.get("/api/requests?scope=all").status_code, 403)
        self.assertEqual([x["id"] for x in c.get("/api/requests?scope=mine").json()], [rid])
        o, r = self.sign_in("olga", "olga-password-1"); ho = {"HTTP_X_CSRFTOKEN": r.json()["csrf"]}
        self.assertEqual([x["id"] for x in o.get("/api/requests?scope=pending").json()], [rid])
        d = o.post(f"/api/requests/{rid}/approve", {"note": "fine"}, content_type="application/json", **ho).json()
        self.assertEqual((d["status"], d["decided_by"]["username"]), ("approved", "olga"))
        self.assertEqual(o.post(f"/api/requests/{rid}/approve", {"note": ""}, content_type="application/json", **ho).status_code, 409)
        self.assertEqual(c.post(f"/api/requests/{rid}/cancel", {"note": "changed my mind"}, content_type="application/json", **h).json()["status"], "cancelled")

    def test_others_requests_hidden_and_stream_needs_sign_in(self, _m):
        r = WorkflowRequest.objects.create(workflow="add-vlan", title="olga's", requested_by=self.op)
        c, _ = self.sign_in("alice", "alice-password-1")
        self.assertEqual(c.get(f"/api/requests/{r.pk}").status_code, 404)
        from django.test import Client
        self.assertEqual(Client().get("/api/stream").status_code, 403)
        self.assertEqual(Client().get("/api/requests").status_code, 401)


# ---- add-vlan: phpIPAM first (0.14.0) ---------------------------------------------------------------------------------------
class FakeIpam:
    """Just enough of phpIPAM's API for the reservation step."""
    def __init__(self, vlans=None, subnets=None, free=("172.16.1.0/24",)):
        self.vlans = vlans or {}; self.subnets = subnets or {}; self.addrs = {}; self.free = list(free); self.writes = []

    def req(self, method, path, json=None):
        path = path.strip("/")
        if method == "POST":
            self.writes.append(path)
            if path == "vlan": self.vlans[json["number"]] = {"vlanId": 900 + json["number"], "number": str(json["number"]), "name": json["name"], "description": json["description"]}
            elif path == "subnets": self.subnets[f"{json['subnet']}/{json['mask']}"] = {"id": 77, "subnet": json["subnet"], "mask": str(json["mask"]), "sectionId": "1", "description": json["description"]}
            elif path == "addresses": self.addrs.setdefault(json["subnetId"], []).append({"ip": json["ip"]})
            return {}
        if path == "l2domains": return [{"id": "5", "name": "evpn-3tier"}]
        if path == "l2domains/5/vlans": return list(self.vlans.values())
        if path == "sections": return [{"id": "1", "name": "evpn-3tier"}]
        if path == "vrf": return [{"vrfId": "3", "name": "app"}]
        if path == "subnets/cidr/172.16.0.0/16": return [{"id": 17, "sectionId": "1"}]
        if path.startswith("subnets/cidr/"):
            k = path.removeprefix("subnets/cidr/"); return [self.subnets[k]] if k in self.subnets else None
        if path.endswith("/addresses"): return self.addrs.get(int(path.split("/")[1]), [])
        raise AssertionError(path)


class AddVlanOrderTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("o", password="o-password-123")
        self.r = WorkflowRequest.objects.create(workflow="add-vlan", title="t", requested_by=u, status="running",
                                                parameters={"vlan_id": 42, "vrf": "app", "vni": 10042, "gateway": "172.16.1.1/24", "span": "leaves",
                                                            "name": "vlan_42_app", "subnet": "172.16.1.0/24"})

    def run_until_nautobot(self, ipam):
        """Run add_vlan with this phpIPAM; stop it where Nautobot would be called (and record the order)."""
        import ipaddress as I
        from . import executors
        from .progress import NullProgress
        order = []
        class Stop(Exception): pass
        def nautobot(*a, **k): order.append("nautobot"); raise Stop()
        log = NullProgress(out=lambda m: order.append("log: " + m)); log.stage = lambda k: order.append(k)
        with mock.patch.object(executors, "Ipam", return_value=ipam), mock.patch.object(executors, "Nautobot", side_effect=nautobot), \
             mock.patch.object(executors.allocate, "free_subnets", return_value=[I.ip_network(x) for x in ipam.free]):
            try: executors.add_vlan(self.r, log)
            except Stop: pass
        return order

    def test_phpipam_first_then_nautobot(self):
        ipam = FakeIpam(); order = self.run_until_nautobot(ipam)
        self.assertEqual([o for o in order if not o.startswith("log")][:2], ["phpipam", "nautobot"])
        self.assertEqual(ipam.writes, ["vlan", "subnets", "addresses"])
        self.assertIn("request #", ipam.subnets["172.16.1.0/24"]["description"])

    def test_taken_since_submitted_changes_nothing(self):
        ipam = FakeIpam(vlans={42: {"vlanId": 1, "number": "42", "name": "someone", "description": "manual"}})
        with self.assertRaisesRegex(RuntimeError, "VLAN 42 was taken"): self.run_until_nautobot(ipam)
        self.assertEqual(ipam.writes, [])
        ipam = FakeIpam(free=())       # the /24 overlaps something reserved since
        with self.assertRaisesRegex(RuntimeError, "no longer free"): self.run_until_nautobot(ipam)
        self.assertEqual(ipam.writes, [])                                             # not even the VLAN

    def test_retry_accepts_its_own_reservation(self):
        ipam = FakeIpam(); self.run_until_nautobot(ipam); n = len(ipam.writes)
        order = self.run_until_nautobot(ipam)
        self.assertIn("nautobot", order); self.assertEqual(len(ipam.writes), n)      # nothing written twice


# ---- remove-vlan (0.15.0) ------------------------------------------------------------------------------------------------------
def _v(vid, vrf, blocked=None, name=None):
    return {"vlan": vid, "name": name or f"vlan_{vid}_{vrf}", "vrf": vrf, "vni": 10000 + vid, "gateway": f"172.16.{vid}.1/24",
            "subnet": f"172.16.{vid}.0/24", "on": ["leaves"], "blocked": blocked}


LAB_V = {**LAB, "vlans": [_v(10, "web", "part of the lab's design (lab.conf)"), _v(41, "web"), _v(42, "app"), _v(55, "db", "hosts attached: x1")]}


@mock.patch("workflows.labmodel.model", return_value=LAB_V)
class RemoveVlanFormTests(TestCase):
    def test_valid_and_params(self, _m):
        f = F.RemoveVlanForm({"vlan": "41", "confirm": "on", "reason": "no longer needed"}); self.assertTrue(f.is_valid(), f.errors)
        p = f.params(); self.assertEqual((p["vlan_id"], p["name"], p["vni"], p["subnet"]), (41, "vlan_41_web", 10041, "172.16.41.0/24"))

    def test_blocked_and_confirm(self, _m):
        for vid, why in (("10", "lab's design"), ("55", "hosts attached")):
            f = F.RemoveVlanForm({"vlan": vid, "confirm": "on", "reason": "x"}); self.assertFalse(f.is_valid()); self.assertIn(why, " ".join(f.errors["vlan"]))
        f = F.RemoveVlanForm({"vlan": "41", "reason": "x"}); self.assertFalse(f.is_valid()); self.assertIn("confirm", f.errors)

    def test_one_open_removal_per_vlan(self, _m):
        u = User.objects.create_user("q", password="q-password-123")
        WorkflowRequest.objects.create(workflow="remove-vlan", title="t", requested_by=u, status="approved", parameters={"vlan_id": 41})
        f = F.RemoveVlanForm({"vlan": "41", "confirm": "on", "reason": "x"}); self.assertFalse(f.is_valid()); self.assertIn("already removes", str(f.errors["vlan"]))


class RemoveVlanModelTests(TestCase):
    def test_blocked_reasons(self):
        from . import labmodel
        ctx = {"lab": "evpn-3tier", "_vlan_names": {41: "vlan_41_web"}, "evpn": {
            "vlans": [{"vlan": 10, "vrf": "web", "vni": 10010, "gateway": "172.16.10.1/24", "on": ["leaves"]},
                      {"vlan": 41, "vrf": "web", "vni": 10041, "gateway": "172.16.0.1/24", "on": ["leaves"]},
                      {"vlan": 44, "vrf": "db", "vni": 10044, "gateway": "172.16.3.1/24", "on": ["leaves"]},
                      {"vlan": 45, "vrf": "app", "vni": 10045, "gateway": "172.16.4.1/24", "on": ["leaves"]},
                      {"vlan": 46, "vrf": "app", "vni": 10046, "gateway": "172.16.5.1/24", "on": ["leaves"]}],
            "vrfs": [], "transit": [], "rt_as": 65500}}
        nodes = [{"name": "h1", "role": "web", "rack": "r", "idx": 1, "asn": None, "peers": [], "vrf": "app", "vlans": [46]}]
        labmodel.invalidate()
        with mock.patch.object(labmodel, "_from_nautobot", return_value=(ctx, ctx["evpn"], nodes, "Nautobot")), \
             mock.patch.object(labmodel, "_base_vlans", return_value={10}):
            m = labmodel.model()
        labmodel.invalidate()
        b = {v["vlan"]: v["blocked"] for v in m["vlans"]}
        self.assertIn("lab.conf", b[10]); self.assertIsNone(b[41]); self.assertIn("last VLAN of VRF db", b[44])
        self.assertIsNone(b[45]); self.assertIn("hosts attached: h1", b[46])


class RemoveVlanExecutorTests(TestCase):
    def test_phpipam_reservations_refuse_before_any_change(self):
        from . import executors
        from .progress import NullProgress
        u = User.objects.create_user("e", password="e-password-123")
        r = WorkflowRequest.objects.create(workflow="remove-vlan", title="t", requested_by=u, status="running",
                                           parameters={"vlan_id": 41, "vni": 10041, "vrf": "web", "subnet": "172.16.0.0/24", "gateway": "172.16.0.1/24", "name": "vlan_41_web"})
        nb = mock.MagicMock(); nb.context.return_value = {"id": "c", "data": {"evpn": {"vlans": [{"vlan": 41, "vrf": "web", "vni": 10041, "gateway": "172.16.0.1/24", "on": ["leaves"]}]}}}
        ipam = FakeIpam(subnets={"172.16.0.0/24": {"id": 77, "subnet": "172.16.0.0", "mask": "24", "sectionId": "1", "description": "x"}})
        ipam.addrs[77] = [{"ip": "172.16.0.1"}, {"ip": "172.16.0.20"}]
        with mock.patch.object(executors, "Nautobot", return_value=nb), mock.patch.object(executors, "Ipam", return_value=ipam), \
             mock.patch("workflows.labmodel.model", return_value={**LAB_V, "vlans": [_v(41, "web")]}):
            with self.assertRaisesRegex(RuntimeError, "172.16.0.20"): executors.remove_vlan(r, NullProgress(out=lambda m: None))
        nb.patch.assert_not_called(); nb.delete.assert_not_called(); self.assertEqual(ipam.writes, [])


# ---- device tools (0.16.0) -------------------------------------------------------------------------------------------------
class DeviceToolTests(TestCase):
    def test_redaction(self):
        from .devices import redact
        t = ("set system login user vyos authentication encrypted-password '$6$abc'\n"
             "set protocols bgp neighbor 1.2.3.4 password 'hunter2'\n"
             "    authentication { encrypted-password $6$xyz }\n"
             "    auth_pass s3cret\n  stats auth admin:pw\n"
             "set service snmp community 'public' authorization ro\nset interfaces ethernet eth1 address '10.0.0.1/31'")
        r = redact(t)
        for secret in ("$6$abc", "hunter2", "$6$xyz", "s3cret", "admin:pw", "'public'"): self.assertNotIn(secret, r)
        self.assertIn("address '10.0.0.1/31'", r)

    def test_parse_vyos_interfaces(self):
        from .devices import parse_vyos_interfaces
        out = """Codes: S - State, L - Link, u - Up, D - Down, A - Admin Down
Interface    IP Address        MAC                VRF        MTU  S/L    Description
-----------  ----------------  -----------------  -------  -----  -----  -----------
br0.10       172.16.10.241/24  52:54:00:d0:03:fe  web       1500  u/u    tier web
             172.16.10.242/24
eth5         -                 52:54:00:d0:03:06  default   1500  u/D    spare
"""
        rows = parse_vyos_interfaces(out)
        self.assertEqual([r["name"] for r in rows], ["br0.10", "eth5"])
        self.assertEqual(rows[0]["addresses"], ["172.16.10.241/24", "172.16.10.242/24"]); self.assertEqual(rows[0]["vrf"], "web")
        self.assertEqual((rows[1]["admin"], rows[1]["link"]), ("up", "down"))

    def test_parse_linux_interfaces(self):
        from .devices import parse_linux_interfaces
        out = "lo UNKNOWN 00:00:00:00:00:00 <LOOPBACK,UP>\nbond0 UP 52:54:00:d0:0c:01 <UP>\neth3 DOWN 52:54:00:d0:0c:03 <>\n\nlo UNKNOWN 127.0.0.1/8\nbond0.10@bond0 UP 172.16.10.21/24\n"
        rows = {r["name"]: r for r in parse_linux_interfaces(out)}
        self.assertEqual(rows["eth3"]["link"], "down"); self.assertEqual(rows["bond0.10"]["addresses"], ["172.16.10.21/24"])

    def test_categories_apply_by_kind_and_role(self):
        from .devices import applies
        leaf = {"kind": "vyos", "role": "leaf"}; spine = {"kind": "vyos", "role": "spine"}; lb = {"kind": "linux", "role": "lb"}; web = {"kind": "linux", "role": "web"}
        self.assertTrue(applies("evpn", leaf)); self.assertFalse(applies("evpn", spine))
        self.assertTrue(applies("bgp", lb)); self.assertFalse(applies("bgp", web)); self.assertTrue(applies("bond", web))
        self.assertFalse(applies("bfd", web)); self.assertTrue(applies("vrrp", lb)); self.assertFalse(applies("vrrp", leaf))

    def test_api_needs_sign_in_and_known_devices(self):
        from django.test import Client
        self.assertEqual(Client().get("/api/devices").status_code, 401)
        u = User.objects.create_user("t", password="t-password-123"); c = Client(); c.force_login(u)
        devs = [{"name": "leaf1", "role": "leaf", "role_label": "Leaf", "kind": "vyos", "mgmt_ip": "10.18.0.21", "rack": "r1", "asn": 1}]
        with mock.patch("workflows.devices.devices", return_value=devs), \
             mock.patch("workflows.devices._run_many", return_value=["set interfaces ethernet eth1 address '10.0.0.1/31'\nset system login user vyos authentication encrypted-password 'x'"]):
            self.assertEqual(c.get("/api/devices/nosuch/config").status_code, 404)
            r = c.get("/api/devices/leaf1/config").json()
            self.assertEqual(r["device"], "leaf1"); self.assertIn("<redacted>", r["text"])
            z = c.get("/api/devices-configs.zip?names=leaf1")
            self.assertEqual(z["Content-Type"], "application/zip")
            import io, zipfile
            self.assertEqual(zipfile.ZipFile(io.BytesIO(z.content)).namelist(), ["leaf1.txt"])


class AsPathTests(TestCase):
    def test_parse_frr_paths(self):
        import json as J
        from .devices import applies, parse_frr_paths
        out = J.dumps({"outside": {"vrfName": "outside", "localAS": 65121, "routes": {
            "0.0.0.0/0": [{"valid": True, "bestpath": True, "selectionReason": "AS Path", "path": "65000", "origin": "IGP", "peerId": "172.31.1.1", "nexthops": [{"ip": "172.31.1.1"}]},
                          {"valid": True, "multipath": True, "path": "65100 65122 65000", "origin": "IGP", "nexthops": [{"ip": "10.61.0.22"}]}],
            "10.255.99.21/32": [{"valid": True, "bestpath": True, "path": "", "origin": "incomplete", "nexthops": [{"ip": "0.0.0.0"}]}]}}})
        rows = parse_frr_paths(out)
        self.assertEqual([(r["prefix"], r["best"], r["as_path"]) for r in rows],
                         [("0.0.0.0/0", True, "65000"), ("0.0.0.0/0", False, "65100 65122 65000"), ("10.255.99.21/32", True, "")])
        self.assertTrue(rows[1]["multipath"]); self.assertEqual(rows[0]["reason"], "AS Path")
        self.assertIsNone(parse_frr_paths("not json"))
        self.assertTrue(applies("aspath", {"kind": "vyos", "role": "spine"})); self.assertTrue(applies("aspath", {"kind": "linux", "role": "lb"}))
        self.assertFalse(applies("aspath", {"kind": "linux", "role": "db"}))


class StandardRuleTests(TestCase):
    def test_for_vlan_merge_drop(self):
        from . import fwrules
        r = fwrules.for_vlan(42, "app", "172.16.1.0/24")
        self.assertEqual(sorted(x["from"] for x in r), ["db", "outside", "web"])
        self.assertTrue(all(x["proto"] == "icmp" and x["dest"] == "172.16.1.0/24" and x["standard"] and x["vlan"] == 42 for x in r))
        custom = [{"from": "outside", "to": "web", "dest": "172.16.10.10", "proto": "tcp", "port": "443"}]
        pol, _ = fwrules.merge(custom, 42, "app", "172.16.1.0/24"); pol2, _ = fwrules.merge(pol, 42, "app", "172.16.1.0/24")
        self.assertEqual(len(pol), 4); self.assertEqual(pol, pol2)                    # idempotent
        kept, n = fwrules.drop(pol, 42); self.assertEqual((kept, n), (custom, 3))
        self.assertEqual(fwrules.rule_number(42), 1042)


class FabricDashboardTests(TestCase):
    def test_helpers(self):
        from . import fabric
        self.assertEqual(fabric._speed("A_10GBASE_T"), 10); self.assertEqual(fabric._speed("A_25GBASE_X_SFP28"), 25); self.assertEqual(fabric._speed("VIRTUAL"), 0)
        self.assertEqual(fabric._link_kind("leaf", "spine"), "fabric"); self.assertEqual(fabric._link_kind("border", "wan"), "wan")
        self.assertEqual(fabric._link_kind("fw", "fw"), "sync"); self.assertEqual(fabric._link_kind("leaf", "web"), "access")
        self.assertEqual(fabric._ratio(30, 20), 1.5); self.assertIsNone(fabric._ratio(30, 0))

    def test_bgp_sessions_counted_once_per_peer(self):
        import json
        from . import fabric
        out = json.dumps({"default": {"ipv4Unicast": {"peers": {"eth1": {"state": "Established"}, "eth2": {"state": "Established"}}},
                                      "l2VpnEvpn": {"peers": {"eth1": {"state": "Established"}, "eth2": {"state": "Active"}}}},
                          "web": {"ipv4Unicast": {"peers": {"172.16.110.4": {"state": "Established"}}}}})
        s = fabric._bgp(out)
        self.assertEqual(len(s), 3)
        self.assertEqual(sorted((x["vrf"], x["peer"], x["up"]) for x in s),
                         [("default", "eth1", True), ("default", "eth2", False), ("web", "172.16.110.4", True)])
        self.assertIsNone(fabric._bgp("not json"))

    def test_api_requires_login(self):
        self.assertEqual(self.client.get("/api/fabric").status_code, 401)
        self.assertEqual(self.client.get("/api/fabric/health").status_code, 401)


class ReconcileTests(TestCase):
    """phpIPAM against Nautobot, on fake data: each kind of drift is found, expected differences are not."""

    def _data(self):
        ip, net = ipaddress.ip_address, ipaddress.ip_network
        P = {"vrfs": {"web", "app"},
             "vlans": {10: {"name": "vlan_10_web", "description": ""}, 41: {"name": "vlan_41_web", "description": "request #6"}, 110: {"name": "transit-web", "description": ""}},
             "subnets": {net("172.16.0.0/16"): {"id": 1, "vrf": None, "vlan": None, "description": "", "container": True},
                         net("172.16.10.0/24"): {"id": 2, "vrf": "web", "vlan": 10, "description": "", "container": False},
                         net("172.16.0.0/24"): {"id": 3, "vrf": "web", "vlan": 41, "description": "request #6", "container": False},
                         net("172.16.110.0/28"): {"id": 4, "vrf": "web", "vlan": 110, "description": "", "container": False},
                         net("10.255.10.0/24"): {"id": 5, "vrf": "web", "vlan": None, "description": "", "container": False}},
             "addresses": {ip("172.16.10.1"): {"subnet": net("172.16.10.0/24"), "gateway": True, "description": "", "hostname": ""},
                           ip("172.16.0.1"): {"subnet": net("172.16.0.0/24"), "gateway": True, "description": "", "hostname": ""},
                           ip("172.16.10.11"): {"subnet": net("172.16.10.0/24"), "gateway": False, "description": "web1", "hostname": ""}}}
        N = {"ctx": {"evpn": {"vrfs": [{"name": "web", "l3vlan": 4010, "probe": "10.255.10"}, {"name": "app", "l3vlan": 4020}],
                              "vlans": [{"vlan": 10, "vrf": "web", "gateway": "172.16.10.1/24"}, {"vlan": 41, "vrf": "web", "gateway": "172.16.0.1/24"}],
                              "transit": [{"vlan": 110, "vrf": "web", "subnet": "172.16.110.0/28"}]}, "lb_peerings": []},
             "vrfs": {"web", "app"},
             "vlans": {10: {"name": "vlan_10_web", "description": ""}, 41: {"name": "vlan_41_web", "description": "request #6"}, 110: {"name": "transit-web", "description": ""}},
             "prefixes": {net("172.16.10.0/24"): {"vrf": "web", "vlan": 10, "description": ""}, net("172.16.0.0/24"): {"vrf": "web", "vlan": 41, "description": "request #6"},
                          net("172.16.110.0/28"): {"vrf": "web", "vlan": 110, "description": ""}},
             "addresses": {ipaddress.ip_interface("172.16.10.11/24"): "web1"}}
        return P, N

    def run_with(self, P, N, open_reqs=()):
        from . import reconcile
        with mock.patch.object(reconcile, "_phpipam", return_value=P), mock.patch.object(reconcile, "_nautobot", return_value=N), \
             mock.patch.object(reconcile, "_open_requests", return_value=set(open_reqs)):
            return reconcile.report(force=True)

    def issues(self, r):
        return {(c["key"], i["object"], i["kind"], i["severity"]) for c in r["checks"] for i in c["issues"]}

    def test_in_sync(self):
        r = self.run_with(*self._data())
        self.assertEqual(self.issues(r), set()); self.assertTrue(r["summary"]["in_sync"])
        self.assertEqual([x["object"] for x in r["ignored"]], ["172.16.0.0/16"])      # the container is not drift

    def test_drift_found(self):
        P, N = self._data(); net, ip = ipaddress.ip_network, ipaddress.ip_address
        P["subnets"][net("172.16.0.0/24")]["vrf"] = "app"                                    # VRF edited by hand in phpIPAM
        P["subnets"][net("172.16.9.0/24")] = {"id": 9, "vrf": "web", "vlan": None, "description": "", "container": False}   # only in phpIPAM
        del P["addresses"][ip("172.16.10.1")]                                                # the gateway released
        P["vlans"][10]["name"] = "web"                                                       # a name difference: a warning
        del N["prefixes"][net("172.16.110.0/28")]                                            # the plan's transit lost its prefix
        del P["addresses"][ip("172.16.10.11")]                                               # an assigned address not reserved
        r = self.run_with(P, N)
        got = self.issues(r)
        for want in [("prefixes", "172.16.0.0/24", "mismatch", "error"), ("prefixes", "172.16.9.0/24", "only_phpipam", "error"),
                     ("gateways", "VLAN 10 gateway 172.16.10.1", "only_nautobot", "error"), ("vlans", "VLAN 10", "mismatch", "warning"),
                     ("prefixes", "172.16.110.0/28", "only_phpipam", "error"), ("plan", "VLAN 110", "mismatch", "error"),
                     ("addresses", "172.16.10.11/24", "only_nautobot", "error")]:
            self.assertIn(want, got)
        self.assertFalse(r["summary"]["in_sync"])

    def test_open_request_is_in_flight(self):
        P, N = self._data()
        del N["prefixes"][ipaddress.ip_network("172.16.0.0/24")]          # request #6 has reserved phpIPAM, not yet written Nautobot
        r = self.run_with(P, N, open_reqs={6})
        self.assertIn(("prefixes", "172.16.0.0/24", "only_phpipam", "in_flight"), self.issues(r))
        self.assertTrue(r["summary"]["in_sync"])                         # in flight is not drift
        r = self.run_with(P, N, open_reqs=set())
        self.assertIn(("prefixes", "172.16.0.0/24", "only_phpipam", "error"), self.issues(r))

    def test_api_requires_login(self):
        self.assertEqual(self.client.get("/api/fabric/reconcile").status_code, 401)


# ---- the change workflows' forms ---------------------------------------------------------------------------------------------
def _vl(vid, name, vrf, subnet, on):
    gw = str(ipaddress.ip_network(subnet).network_address + 1)
    return {"vlan": vid, "name": name, "vrf": vrf, "vni": 10000 + vid, "gateway": f"{gw}/24", "subnet": subnet, "on": on, "blocked": None}


LAB2 = {**LAB,
        "vlans": [_vl(10, "vlan_10_web", "web", "172.16.10.0/24", ["leaves", "borders"]), _vl(20, "vlan_20_app", "app", "172.16.20.0/24", ["leaves"]),
                  _vl(30, "vlan_30_db", "db", "172.16.30.0/24", ["leaves"]), _vl(42, "vlan_42_app", "app", "172.16.1.0/24", ["leaves"]),
                  _vl(47, "vlan_47_web", "web", "172.16.6.0/24", ["leaves"])],
        "hosts": [{"name": "lb1", "role": "lb", "vrf": "web", "vlan": 10, "vlans": [10], "address": "172.16.10.5/24", "hostnum": 5, "peers": ["border1", "border2"]},
                  {"name": "web1", "role": "web", "vrf": "web", "vlan": 10, "vlans": [10], "address": "172.16.10.11/24", "hostnum": 11, "peers": ["leaf1", "leaf2"]},
                  {"name": "app1", "role": "app", "vrf": "app", "vlan": 20, "vlans": [20], "address": "172.16.20.11/24", "hostnum": 11, "peers": ["leaf1", "leaf2"]},
                  {"name": "db1", "role": "db", "vrf": "db", "vlan": 30, "vlans": [30], "address": "172.16.30.11/24", "hostnum": 11, "peers": ["leaf1", "leaf2"]}],
        "fw_policy": [{"from": "outside", "to": "web", "dest": "172.16.10.10", "proto": "tcp", "port": "443"},
                      {"from": "web", "to": "app", "dest": "172.16.20.0/24", "proto": "tcp", "port": "8080"},
                      {"from": "app", "to": "db", "dest": "172.16.30.0/24", "proto": "tcp", "port": "5432"},
                      {"from": "outside", "to": "app", "dest": "172.16.20.0/24", "proto": "icmp", "port": None, "standard": True, "vlan": 20},
                      {"from": "web", "to": "db", "dest": "172.16.30.11", "proto": "tcp", "port": "9000-9010", "rule": 500, "request": 21}],
        "web_vip": "172.16.10.10"}
patched2 = mock.patch("workflows.labmodel.model", return_value=LAB2)


@patched2
class ChangeFormTests(TestCase):
    def test_span(self, _m):
        f = F.VlanSpanForm({"vlan": "47", "span": "leaves+borders", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)
        p = f.params(); self.assertEqual((p["span_from"], p["span_to"]), ("leaves", "leaves+borders"))
        self.assertFalse(F.VlanSpanForm({"vlan": "47", "span": "leaves", "reason": "r"}).is_valid())          # no change
        f = F.VlanSpanForm({"vlan": "10", "span": "leaves", "reason": "r"}); self.assertFalse(f.is_valid())  # lb1 uses it on the borders
        self.assertIn("lb1", str(f.errors))

    def test_fw_rule_valid_and_normalised(self, _m):
        f = F.FwRuleForm({"source": "web", "target": "db", "proto": "tcp", "dest": "172.16.30.12/32", "port": "6432", "reason": "r"})
        self.assertTrue(f.is_valid(), f.errors); p = f.params()
        self.assertEqual((p["dest"], p["port"], p["port_label"]), ("172.16.30.12", "6432", "port 6432"))

    def test_fw_rule_refusals(self, _m):
        bad = [({"source": "web", "target": "web", "proto": "tcp", "dest": "172.16.10.11", "port": "80"}, "same zone"),
               ({"source": "web", "target": "db", "proto": "tcp", "dest": "172.16.20.11", "port": "80"}, "not in zone db"),
               ({"source": "web", "target": "app", "proto": "tcp", "dest": "172.16.20.11", "port": "8080"}, "Already allowed"),
               ({"source": "web", "target": "db", "proto": "tcp", "dest": "172.16.30.11", "port": "9005"}, "Already allowed"),   # inside a range
               ({"source": "outside", "target": "app", "proto": "icmp", "dest": "172.16.20.11", "port": ""}, "Standard Firewall Rule"),
               ({"source": "web", "target": "db", "proto": "icmp", "dest": "172.16.30.11", "port": "7"}, "no port"),
               ({"source": "web", "target": "db", "proto": "tcp", "dest": "172.16.30.11", "port": ""}, "need a port"),
               ({"source": "app", "target": "outside", "proto": "tcp", "dest": "172.16.0.0/16", "port": "443"}, "inside the lab"),
               ({"source": "app", "target": "outside", "proto": "tcp", "dest": "0.0.0.0/0", "port": "443"}, "/8 or longer")]
        for data, why in bad:
            f = F.FwRuleForm({**data, "reason": "r"}); self.assertFalse(f.is_valid(), data); self.assertIn(why, str(f.errors), data)

    def test_fw_rule_remove_lists_only_self_service_rules(self, _m):
        f = F.FwRuleRemoveForm()
        self.assertEqual([c for c, _ in f.fields["rule"].choices], ["web|db|500"])
        f = F.FwRuleRemoveForm({"rule": "web|db|500", "confirm": "on", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual((f.params()["rule"], f.params()["added_by"]), (500, 21))

    def test_move_server(self, _m):
        f = F.MoveServerForm({"server": "web1", "vlan": "47", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)
        p = f.params(); self.assertEqual((p["from_vlan"], p["to_vlan"], p["to_address"]), (10, 47, "172.16.6.11/24"))
        for data, why in [({"server": "web1", "vlan": "42"}, "within its VRF"), ({"server": "web1", "vlan": "10"}, "already in VLAN"),
                          ({"server": "db1", "vlan": "30"}, "database servers"), ({"server": "lb1", "vlan": "47"}, "load balancers")]:
            f = F.MoveServerForm({**data, "reason": "r"}); self.assertFalse(f.is_valid(), data); self.assertIn(why, str(f.errors), data)


class TwinRuleTests(TestCase):
    def test_rules_follow_a_moved_server(self):
        from .changes import _twins
        pol = LAB2["fw_policy"]
        tw = _twins(pol, "app1", "172.16.20.11", "172.16.1.11")
        self.assertEqual([(t["from"], t["to"], t["dest"], t["port"], t["follows"]) for _, t in tw], [("web", "app", "172.16.1.11", "8080", "app1")])
        self.assertEqual(_twins(pol, "app1", "172.16.20.11", "172.16.20.99"), [])     # the same subnet: already covered
        done = pol + [{**tw[0][1], "rule": 500}]
        self.assertEqual(_twins(done, "app1", "172.16.20.11", "172.16.1.11"), [])      # a retry adds no second twin
        back = pol + [{**tw[0][1], "rule": 500}]                                         # moving back: the subnet rule covers it again
        self.assertEqual(_twins(back, "app1", "172.16.1.11", "172.16.20.11"), [])


class DatabaseToolTests(TestCase):
    """The ASN and VLAN databases: Excel workbooks with the right sheets, and the API behind a login."""

    def test_workbooks(self):
        import io
        from openpyxl import load_workbook
        from . import dbtools
        asn = {"rows": [{"asn": 65100, "kind": "Fabric", "private": True, "devices": ["spine1", "spine2"], "roles": ["Spine"], "racks": ["spine"],
                         "router_ids": ["10.61.0.1"], "peers": [65101], "peer_devices": ["leaf1"], "sessions": 2, "vrfs": ["default"], "source": "Nautobot"}],
               "sessions": [{"a": "spine1", "a_asn": 65100, "a_addr": "eth1", "b": "leaf1", "b_asn": 65101, "b_addr": "eth1", "vrf": "default",
                             "kind": "Underlay + EVPN", "bfd": True, "note": ""}],
               "fetched": "2026-11-09T00:00:00Z", "summary": {"asns": 1, "speakers": 1, "sessions": 1, "private_used": 1, "private_pool": 1023}}
        vl = {"rows": [{"vlan": 10, "name": "vlan_10_web", "type": "Tier", "vrf": "web", "vni": 10010, "subnet": "172.16.10.0/24", "gateway": "172.16.10.1",
                        "size": 254, "where": "every leaf", "hosts": ["web1"], "tenant": "e3t-web", "status": "Active", "origin": "lab design",
                        "request": None, "description": "", "in_nautobot": True}],
              "fetched": "2026-11-09T00:00:00Z", "summary": {"vlans": 1, "by_type": {"Tier": 1}, "vrfs": 4, "subnets": 1, "self_service": 0}}
        with mock.patch.object(dbtools, "asn_db", return_value=asn), mock.patch.object(dbtools, "vlan_db", return_value=vl):
            a = load_workbook(io.BytesIO(dbtools.asn_xlsx())); v = load_workbook(io.BytesIO(dbtools.vlan_xlsx()))
        self.assertEqual(a.sheetnames, ["ASNs", "BGP sessions", "About"]); self.assertEqual(v.sheetnames, ["VLANs", "About"])
        self.assertEqual([c.value for c in a["ASNs"][2]][:4], [65100, "Fabric", "yes", "spine1, spine2"])
        self.assertEqual([c.value for c in v["VLANs"][2]][:6], [10, "vlan_10_web", "Tier", "web", 10010, "172.16.10.0/24"])
        self.assertEqual(a["ASNs"].freeze_panes, "A2")

    def test_api_requires_login(self):
        for u in ("/api/tools/asns", "/api/tools/asns.xlsx", "/api/tools/vlans", "/api/tools/vlans.xlsx"):
            self.assertEqual(self.client.get(u).status_code, 401, u)


def _port(sw, name, kind=None, peer=None, enabled=True, mode=None, untagged=None, tagged=()):
    return {"switch": sw, "name": name, "kind": kind, "peer": peer, "peer_port": "eth1" if peer else None, "enabled": enabled, "mode": mode,
            "untagged_vlan": untagged, "tagged_vlans": list(tagged), "description": "spare port (Port Provisioning)" if kind is None else f"{kind}: {peer}"}


LAB3 = {**LAB2, "ports": [_port("leaf1", "eth1", "fabric", "spine1"), _port("leaf1", "eth3", "access", "web1"), _port("leaf2", "eth3", "access", "web1"),
                          _port("leaf1", "eth4", "access", "app1"), _port("leaf2", "eth4", "access", "app1", enabled=False),
                          _port("leaf1", "eth6", enabled=False), _port("leaf1", "eth7", mode="access", untagged=47)]}
patched3 = mock.patch("workflows.labmodel.model", return_value=LAB3)


APPS = [{"id": "t1", "app_id": "APP-1001", "name": "Order Desk", "description": "", "owner": {"id": "g1", "name": "Order Desk team", "email": ""}, "label": "APP-1001 · Order Desk"}]
PROFS = [{"name": "web-trunk", "description": "", "mode": "tagged", "access_vlan": None, "trunk_vlans": [42, 47], "native_vlan": 10}]
LAB3["port_profiles"] = PROFS
patched_apps = mock.patch("workflows.ownership.applications", return_value=APPS)


@patched3
@patched_apps
class PortFormTests(TestCase):
    def form(self, **d):
        return F.PortForm({"reason": "r", **d})

    def test_uplink_is_blocked(self, _a, _m):
        f = self.form(port="leaf1|eth1", state="disabled"); self.assertFalse(f.is_valid()); self.assertIn("uplink to spine1", str(f.errors))
        self.assertIn("blocked", dict(F.PortForm().fields["port"].choices)["leaf1|eth1"])

    def test_spare_by_hand_needs_an_owner(self, _a, _m):
        f = self.form(port="leaf1|eth6", state="enabled", mode="access", access_vlan="47"); self.assertFalse(f.is_valid()); self.assertIn("needs an owner", str(f.errors))
        f = self.form(port="leaf1|eth6", state="enabled", mode="access", access_vlan="47", application="APP-1001", description="backup appliance")
        self.assertTrue(f.is_valid(), f.errors); p = f.params()
        self.assertEqual((p["mode"], p["untagged_vlan"], p["application"]["app_id"], p["owner"]), ("access", 47, "APP-1001", "Order Desk team"))
        self.assertEqual(p["description"], "APP-1001 Order Desk — backup appliance")

    def test_trunk_with_native(self, _a, _m):
        f = self.form(port="leaf1|eth6", state="enabled", mode="tagged", trunk_vlans=["47", "20"], native_vlan="10", application="APP-1001"); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual((f.params()["trunk_vlans"], f.params()["native_vlan"], f.params()["untagged_vlan"]), ([20, 47], 10, 10))
        f = self.form(port="leaf1|eth6", state="enabled", mode="tagged", trunk_vlans=["47"], native_vlan="47", application="APP-1001"); self.assertFalse(f.is_valid())
        self.assertIn("both tagged", str(f.errors))

    def test_profile(self, _a, _m):
        f = self.form(port="leaf1|eth6", state="enabled", profile="web-trunk", application="APP-1001"); self.assertTrue(f.is_valid(), f.errors); p = f.params()
        self.assertEqual((p["profile"], p["mode"], p["trunk_vlans"], p["native_vlan"]), ("web-trunk", "tagged", [42, 47], 10))
        f = self.form(port="leaf1|eth6", state="enabled", profile="web-trunk", mode="access", access_vlan="47", application="APP-1001"); self.assertFalse(f.is_valid())
        self.assertIn("sets the mode", str(f.errors))

    def test_default(self, _a, _m):
        f = self.form(port="leaf1|eth7", state="default"); self.assertTrue(f.is_valid(), f.errors); p = f.params()
        self.assertEqual((p["enabled"], p["mode"], p["trunk_vlans"], p["profile"], p["application"], p["description"]), (False, None, [], None, None, F.SPARE_DESC))
        f = self.form(port="leaf1|eth6", state="default"); self.assertFalse(f.is_valid()); self.assertIn("nothing to change", str(f.errors))
        f = self.form(port="leaf1|eth7", state="default", application="APP-1001"); self.assertFalse(f.is_valid()); self.assertIn("Default clears", str(f.errors))
        f = self.form(port="leaf1|eth3", state="default"); self.assertFalse(f.is_valid()); self.assertIn("use Enable", str(f.errors))

    def test_spare_refusals(self, _a, _m):
        for d, why in [(dict(state="enabled", application="APP-1001"), "needs a configuration"), (dict(state="enabled", mode="access", application="APP-1001"), "choose the access VLAN"),
                       (dict(state="enabled", mode="tagged", application="APP-1001"), "at least one VLAN"),
                       (dict(state="enabled", mode="access", access_vlan="110", application="APP-1001"), "valid choice")]:
            f = self.form(port="leaf1|eth6", **d); self.assertFalse(f.is_valid(), d); self.assertIn(why, str(f.errors), d)

    def test_server_leg(self, _a, _m):
        f = self.form(port="leaf1|eth3", state="disabled"); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual((f.params()["kind"], f.params()["partner"]), ("server leg", "leaf2 eth3"))
        f = self.form(port="leaf1|eth4", state="disabled"); self.assertFalse(f.is_valid()); self.assertIn("would cut app1 off", str(f.errors))
        f = self.form(port="leaf1|eth3", state="enabled", mode="tagged", trunk_vlans=["47"]); self.assertFalse(f.is_valid()); self.assertIn("follow the server", str(f.errors))


@patched3
class PortProfileFormTests(TestCase):
    def test_create_change_delete(self, _m):
        f = F.PortProfileForm({"action": "create", "name": "Web-Access", "mode": "access", "access_vlan": "47", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.params()["profile"], {"name": "web-access", "description": "", "mode": "access", "access_vlan": 47, "trunk_vlans": [], "native_vlan": None})
        self.assertFalse(F.PortProfileForm({"action": "create", "name": "web-trunk", "mode": "access", "access_vlan": "47", "reason": "r"}).is_valid())
        users = [dict(p, profile="web-trunk") if p["name"] == "eth7" else p for p in LAB3["ports"]]
        with mock.patch("workflows.labmodel.model", return_value={**LAB3, "ports": users}):
            f = F.PortProfileForm({"action": "update", "name": "web-trunk", "mode": "tagged", "trunk_vlans": ["42"], "reapply": "on", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)
            self.assertEqual(f.params()["ports"], ["leaf1 eth7"])
            f = F.PortProfileForm({"action": "delete", "name": "web-trunk", "reason": "r"}); self.assertFalse(f.is_valid()); self.assertIn("in use on leaf1 eth7", str(f.errors))
        f = F.PortProfileForm({"action": "delete", "name": "web-trunk", "reason": "r"}); self.assertTrue(f.is_valid(), f.errors)


class LeafPairTests(TestCase):
    """Add / Remove a leaf pair: allocation from the leaf number, the next pair, the last pair, the forms' refusals."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp())
        p = mock.patch("workflows.leafpair.settings"); s = p.start(); self.addCleanup(p.stop); s.LAB_DIR = self.dir
        p = mock.patch("workflows.leafpair.mem_available_mib", return_value=20000); p.start(); self.addCleanup(p.stop)

    def racked(self, *leaves):
        (self.dir / "added-leaves.conf").write_text(f"ADDED_LEAVES=({' '.join(leaves)})\n")

    def test_allocation(self):
        from . import leafpair as L
        a = L.allocation(5)
        self.assertEqual((a["idx"], a["mgmt_ip"], a["loopback"], a["asn"], a["rack"], a["spine_port"], a["console"]),
                         (21, "10.18.0.25", "10.61.0.15", 65105, "rack3", "eth7", 5641))
        self.assertEqual((L.allocation(8)["rack"], L.allocation(8)["spine_port"]), ("rack4", "eth10"))

    def test_next_and_last(self):
        from . import leafpair as L
        self.assertEqual([a["name"] for a in L.next_pair()], ["leaf5", "leaf6"]); self.assertIsNone(L.last_pair())
        self.racked("leaf5", "leaf6")
        self.assertEqual([a["name"] for a in L.next_pair()], ["leaf7", "leaf8"]); self.assertEqual([a["name"] for a in L.last_pair()], ["leaf5", "leaf6"])
        self.racked("leaf5", "leaf6", "leaf7", "leaf8")
        self.assertIsNone(L.next_pair()); self.assertEqual([a["name"] for a in L.last_pair()], ["leaf7", "leaf8"])

    def test_add_form(self):
        f = F.AddLeafPairForm(data={"pair": "5,6", "reason": "a new rack"})
        self.assertTrue(f.is_valid(), f.errors); self.assertEqual(f.params()["ks"], [5, 6]); self.assertEqual(f.params()["rack"], "rack3")
        self.racked("leaf5", "leaf6", "leaf7", "leaf8")
        f = F.AddLeafPairForm(data={"pair": "5,6", "reason": "x"})
        self.assertFalse(f.is_valid()); self.assertIn("no spare ports left", f.errors.as_text())

    def test_add_form_memory(self):
        with mock.patch("workflows.leafpair.mem_available_mib", return_value=3000):
            f = F.AddLeafPairForm(data={"pair": "5,6", "reason": "x"})
            self.assertFalse(f.is_valid()); self.assertIn("MiB available", f.errors.as_text())

    def test_remove_form(self):
        f = F.RemoveLeafPairForm(data={"pair": "", "confirm": "rack3", "reason": "x"})
        self.assertFalse(f.is_valid()); self.assertIn("original leaves cannot be removed", f.errors.as_text())
        self.racked("leaf5", "leaf6")
        lab = {**LAB, "ports": [{"switch": "leaf5", "name": "eth3", "kind": None, "enabled": True, "mode": "access", "application": {"app_id": "APP-1042"}},
                                {"switch": "leaf6", "name": "eth3", "kind": None, "enabled": False, "mode": None}]}
        with mock.patch("workflows.labmodel.model", return_value=lab):
            f = F.RemoveLeafPairForm(data={"pair": "5,6", "confirm": "rack9", "reason": "x"})
            self.assertFalse(f.is_valid()); self.assertIn("Type rack3", f.errors.as_text())
            f = F.RemoveLeafPairForm(data={"pair": "5,6", "confirm": "rack3", "reason": "x"})
            self.assertFalse(f.is_valid()); self.assertIn("leaf5 eth3 is provisioned (APP-1042)", f.errors.as_text())
        lab["ports"][0].update(enabled=False, mode=None, application=None)
        lab["ports"].append({"switch": "leaf6", "name": "eth4", "kind": "access", "peer": "web3"})
        with mock.patch("workflows.labmodel.model", return_value=lab):
            f = F.RemoveLeafPairForm(data={"pair": "5,6", "confirm": "rack3", "reason": "x"})
            self.assertFalse(f.is_valid()); self.assertIn("web3 is cabled to it", f.errors.as_text())
        lab["ports"].pop()
        with mock.patch("workflows.labmodel.model", return_value=lab):
            f = F.RemoveLeafPairForm(data={"pair": "5,6", "confirm": "rack3", "reason": "x"})
            self.assertTrue(f.is_valid(), f.errors); self.assertEqual(f.params()["leaves"], "leaf5 + leaf6")


class VrfTests(TestCase):
    """Add / Delete a VRF: allocation, names, clashes, what keeps a VRF from being deleted, the zone's standard rules."""
    DATA = {"evpn": {"rt_as": 65500, "vrfs": [{"name": n, "l3vni": 50000 + t, "table": 1000 + t, "l3vlan": 4000 + t, "probe": f"10.255.{t}"}
                                              for n, t in (("web", 10), ("app", 20), ("db", 30), ("outside", 99))],
                     "transit": [{"vrf": n, "vlan": v} for n, v in (("web", 110), ("app", 120), ("db", 130), ("outside", 199))],
                     "vlans": [{"vlan": 10, "vrf": "web", "gateway": "172.16.10.1/24"}, {"vlan": 20, "vrf": "app", "gateway": "172.16.20.1/24"}]},
            "fw_policy": [], "lb_peerings": []}

    def test_allocation(self):
        from . import vrfs as V
        a = V.allocation(1, "pay")
        self.assertEqual((a["table"], a["l3vni"], a["l3vlan"], a["transit_vlan"], a["transit_vni"], a["subnet"], a["probe"], a["borders"], a["fws"]),
                         (2001, 51001, 3901, 3801, 13801, "172.16.240.0/28", "10.255.101", ["172.16.240.2", "172.16.240.3"], ["172.16.240.11", "172.16.240.12"]))
        self.assertEqual(V.allocation(20, "x")["subnet"], "172.16.241.48/28")
        self.assertTrue(all(V.allocation(z, "x")["subnet"] != V.allocation(z + 1, "x")["subnet"] for z in range(1, 20)))

    def test_next_z_and_limit(self):
        import copy
        from . import vrfs as V
        d = copy.deepcopy(self.DATA); self.assertEqual(V.next_z(d), 1)
        d["evpn"]["vrfs"] += [{"name": f"v{z}", "z": z} for z in (1, 2, 4)]; self.assertEqual(V.next_z(d), 3)
        d["evpn"]["vrfs"] += [{"name": f"v{z}", "z": z} for z in range(5, 21)] + [{"name": "v3", "z": 3}]; self.assertIsNone(V.next_z(d))

    def test_clashes(self):
        from . import vrfs as V
        m = {"vlans_used": [3801], "vnis_used": [51001], "subnets_used": ["172.16.240.0/24"]}
        why = V.clashes(V.allocation(1, "web"), m, self.DATA)
        self.assertTrue(any("VRF web exists" in w for w in why)); self.assertTrue(any("VLAN 3801" in w for w in why))
        self.assertTrue(any("VNI 51001" in w for w in why)); self.assertTrue(any("overlaps" in w for w in why))

    def test_in_use(self):
        import copy
        from . import vrfs as V
        d = copy.deepcopy(self.DATA)
        d["evpn"]["vlans"].append({"vlan": 48, "vrf": "pay", "gateway": "172.16.3.1/24"})
        d["fw_policy"] = [{"from": "pay", "to": "web", "dest": "172.16.10.0/24", "proto": "icmp", "standard": True, "vlan": 10},
                          {"from": "web", "to": "pay", "dest": "172.16.3.0/24", "proto": "tcp", "port": 443}]
        why = V.in_use("pay", {"hosts": [{"name": "pay1", "vrf": "pay"}]}, d)
        self.assertIn("VLAN 48", why); self.assertIn("server pay1", why); self.assertIn("firewall rule web -> pay 172.16.3.0/24 tcp 443", why)
        self.assertEqual(len(why), 3)                                   # the standard rule does not block: it goes with the zone

    def test_zone_rules(self):
        from . import fwrules
        pol, n = fwrules.add_zone([], "pay", self.DATA["evpn"]["vlans"])
        self.assertEqual(n, 2); self.assertEqual({(r["from"], r["to"], r["vlan"]) for r in pol}, {("pay", "web", 10), ("pay", "app", 20)})
        self.assertEqual(fwrules.add_zone(pol, "pay", self.DATA["evpn"]["vlans"])[1], 0)
        pol += fwrules.for_vlan(48, "pay", "172.16.3.0/24", ["web", "app", "db", "outside", "pay"])
        self.assertEqual(len(pol), 6)
        pol, n = fwrules.drop_zone(pol + [{"from": "web", "to": "pay", "proto": "tcp", "port": 443}], "pay")
        self.assertEqual((n, len(pol)), (6, 1))                        # custom rules stay (in_use refuses the delete first)

    def test_forms(self):
        with mock.patch("workflows.labmodel.model", return_value={**LAB, "vlans_used": [], "vnis_used": [], "subnets_used": []}), \
             mock.patch("workflows.clients.Nautobot") as nb:
            nb.return_value.context.return_value = {"data": self.DATA}
            for bad, msg in (("web", "reserved"), ("x", "2-8"), ("averylongname", "at most 8")):
                f = F.AddVrfForm(data={"vrf": bad, "reason": "x"}); self.assertFalse(f.is_valid()); self.assertIn(msg, f.errors.as_text())
            f = F.AddVrfForm(data={"vrf": "pay", "reason": "x"})
            self.assertTrue(f.is_valid(), f.errors); self.assertEqual((f.params()["z"], f.params().get("vlan_id")), (1, None))
            f = F.DeleteVrfForm(data={"vrf": "web", "confirm": "web", "reason": "x"}); self.assertFalse(f.is_valid())
