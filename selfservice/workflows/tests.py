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
}
patched = mock.patch("workflows.labmodel.model", return_value=LAB)

VLAN_OK = {"vlan_id": 40, "name": "app-backup", "vrf": "app", "gateway": "172.16.40.1/24", "span": "leaves", "reason": "backups"}
LB_OK = {"device": "lb1", "asn": 65310, "peering_vlan": 150, "peering_subnet": "172.16.150.0/29",
         "vip_prefixes": "172.16.200.0/28\n172.16.201.0/28", "bfd": "on", "reason": "a second load-balancer pair"}


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
class LbPeeringFormTests(TestCase):
    def test_valid(self, _m):
        f = F.LbPeeringForm(LB_OK); self.assertTrue(f.is_valid(), f.errors)
        p = f.params()
        self.assertEqual(p["vip_prefixes"], ["172.16.200.0/28", "172.16.201.0/28"])
        self.assertEqual((p["attach_to"], p["switches"], p["vrf"], p["vni"]), ("border leaves", ["border1", "border2"], "web", 10150))

    def test_device_already_peering(self, _m):
        f = F.LbPeeringForm({**LB_OK, "device": "lb2"}); self.assertFalse(f.is_valid()); self.assertIn("device", f.errors)

    def test_asn_in_use(self, _m):
        f = F.LbPeeringForm({**LB_OK, "asn": 65121}); self.assertFalse(f.is_valid()); self.assertIn("asn", f.errors)

    def test_vip_overlaps_a_tier(self, _m):
        f = F.LbPeeringForm({**LB_OK, "vip_prefixes": "172.16.10.0/28"}); self.assertFalse(f.is_valid()); self.assertIn("vip_prefixes", f.errors)

    def test_vip_not_a_prefix(self, _m):
        f = F.LbPeeringForm({**LB_OK, "vip_prefixes": "172.16.200.5/28"}); self.assertFalse(f.is_valid())

    def test_peering_and_vips_overlap(self, _m):
        f = F.LbPeeringForm({**LB_OK, "vip_prefixes": "172.16.150.0/28"}); self.assertFalse(f.is_valid())


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
        self.submit(self.alice, "lb-peering", LB_OK)
        self.assertEqual(WorkflowRequest.objects.get().title, "Peer load balancer lb1 (AS 65310) with the border leaves")

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
        ok = c.post("/api/requests", {"workflow": "lb-peering", "data": {**LB_OK, "bfd": True}}, content_type="application/json", **h)
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
