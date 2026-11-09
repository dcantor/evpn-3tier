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
}
patched = mock.patch("workflows.labmodel.model", return_value=LAB)

VLAN_OK = {"vlan_id": 40, "name": "app-backup", "vrf": "app", "gateway": "172.16.40.1/24", "span": "leaves", "reason": "backups"}
LB_OK = {"name": "lb3", "attach_to": "border leaves", "vrf": "web", "asn": 65310, "peering_vlan": 150, "peering_subnet": "172.16.150.0/29",
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

    def test_outside_vrf_not_offered(self, _m):
        f = F.AddVlanForm({**VLAN_OK, "vrf": "outside"}); self.assertFalse(f.is_valid()); self.assertIn("vrf", f.errors)


@patched
class LbPeeringFormTests(TestCase):
    def test_valid(self, _m):
        f = F.LbPeeringForm(LB_OK); self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.params()["vip_prefixes"], ["172.16.200.0/28", "172.16.201.0/28"])

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
        self.assertEqual((wr.title, wr.status, wr.requested_by), ("Add VLAN 40 (app-backup) to VRF app", "submitted", self.alice))
        self.assertEqual(wr.parameters["vni"], 10040)
        self.assertEqual(list(wr.events.values_list("kind", flat=True)), ["submitted"])

    def test_lb_request(self, _m):
        self.submit(self.alice, "lb-peering", LB_OK)
        self.assertEqual(WorkflowRequest.objects.get().title, "Peer load balancer lb3 (AS 65310) with the border leaves")

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
