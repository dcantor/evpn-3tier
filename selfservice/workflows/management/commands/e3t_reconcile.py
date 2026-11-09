"""Compare phpIPAM with Nautobot (workflows/reconcile.py) and print the differences. Read-only.
Exit status: 0 in sync (warnings allowed), 1 drift (errors), 2 a source did not answer.
   manage.py e3t_reconcile [--json]"""
import json
import sys

from django.core.management.base import BaseCommand

from workflows import reconcile


class Command(BaseCommand):
    help = "compare phpIPAM with Nautobot (read-only)"

    def add_arguments(self, p):
        p.add_argument("--json", action="store_true", help="the whole report as JSON")

    def handle(self, *a, **o):
        try: r = reconcile.report(force=True)
        except Exception as e:                                                # noqa: BLE001
            self.stderr.write(f"reconcile: {type(e).__name__}: {e}"); sys.exit(2)
        if o["json"]:
            self.stdout.write(json.dumps(r, indent=1))
        else:
            s = r["summary"]
            self.stdout.write(f"phpIPAM ↔ Nautobot: {s['matched']}/{s['total']} match · {s['errors']} errors · {s['warnings']} warnings · {s['in_flight']} in flight")
            for c in r["checks"]:
                self.stdout.write(f"  {c['label']:<26} {c['matched']}/{c['total']}")
                for i in c["issues"]:
                    vals = f"  (phpIPAM: {i['phpipam']}; Nautobot: {i['nautobot']})" if i["phpipam"] or i["nautobot"] else ""
                    self.stdout.write(f"    {i['severity'].upper():<9} {i['object']}: {i['detail']}{vals}")
            for x in r["ignored"]: self.stdout.write(f"  ignored    {x['object']}: {x['why']}")
        sys.exit(0 if r["summary"]["in_sync"] else 1)
