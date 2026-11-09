"""Create what Port Provisioning's applications, owners and profiles need in Nautobot (idempotent; workflows/ownership.py)."""
from django.core.management.base import BaseCommand

from workflows import ownership


class Command(BaseCommand):
    help = "create the Nautobot schema for applications, owners and port profiles (idempotent)"

    def handle(self, *a, **o):
        done = []
        ownership.ensure_schema(lambda m: (done.append(m), self.stdout.write(f"created: {m}")))
        if not done: self.stdout.write("nothing to do: everything exists")
