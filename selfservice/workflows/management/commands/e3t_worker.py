"""The worker: carries out approved requests, one at a time, oldest first (workflows/executors.py). Every step goes into
the request's history. A request found `running` at start-up was interrupted (the worker restarted): it is marked
failed — its steps are idempotent, so an operator can retry it.   manage.py e3t_worker [--once]"""
import time
import traceback

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workflows.catalog import BY_SLUG
from workflows.executors import EXECUTORS, NotAutomated
from workflows.progress import Progress
from workflows.models import WorkflowRequest as R


class Command(BaseCommand):
    help = "carry out approved workflow requests"

    def add_arguments(self, p):
        p.add_argument("--once", action="store_true", help="handle what is queued, then exit")

    def handle(self, *a, once=False, **kw):
        for r in R.objects.filter(status=R.Status.RUNNING):
            r.status = R.Status.FAILED
            if r.progress: r.progress = {**r.progress, "current": None, "stages": [{**s, "status": "failed"} if s["status"] == "running" else s for s in r.progress.get("stages", [])]}
            r.save(update_fields=["status", "progress", "updated"])
            r.log(None, "failed", "interrupted: the worker restarted while it ran (its steps can be retried)")
        self.stdout.write("worker: waiting for approved requests")
        while True:
            with transaction.atomic():
                r = R.objects.select_for_update().filter(status=R.Status.APPROVED).order_by("decided_at", "pk").first()
                if r: r.status = R.Status.RUNNING; r.save(update_fields=["status", "updated"])
            if r: self.carry_out(r)
            elif once: return
            else: time.sleep(3)

    def carry_out(self, r):                    # (not "execute": that is BaseCommand's own)
        self.stdout.write(f"#{r.pk} {r.title}: running")
        r.log(None, "started", "the worker started carrying it out")
        prog = Progress(r, BY_SLUG[r.workflow].stages)
        try:
            result = EXECUTORS[r.workflow](r, prog) or {}
            r.result = {**result, "finished": timezone.now().isoformat()}; r.status = R.Status.DONE
            prog.finish(True)
            r.log(None, "done", "carried out")
        except NotAutomated as e:
            r.status = R.Status.FAILED; r.result = {"not_automated": True}; prog.finish(False)
            r.log(None, "not automated", str(e))
        except Exception as e:                                               # noqa: BLE001
            r.status = R.Status.FAILED; r.result = {"error": f"{type(e).__name__}: {e}"}; prog.finish(False)
            r.log(None, "failed", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}")
        r.save(update_fields=["status", "result", "updated"])
        self.stdout.write(f"#{r.pk}: {r.status}")
