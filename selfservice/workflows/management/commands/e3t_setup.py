"""First-time setup, idempotent: the secret key and the admin account (random password) in
~/.config/evpn-3tier/selfservice.env (mode 600, not in git), and the groups:
  requesters  submit requests, see and cancel their own
  operators   see every request, approve or reject them (approved ones are carried out by the worker)"""
import secrets

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "secret key, admin account, groups (idempotent)"

    def handle(self, *a, **kw):
        env = dict(settings.ENV)
        new = False
        if "SECRET_KEY" not in env: env["SECRET_KEY"] = secrets.token_urlsafe(50); new = True
        if "ADMIN_PASSWORD" not in env: env["ADMIN_USER"], env["ADMIN_PASSWORD"] = "admin", secrets.token_urlsafe(12); new = True
        if new:
            settings.ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            settings.ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in env.items())); settings.ENV_FILE.chmod(0o600)
        u, created = User.objects.get_or_create(username=env["ADMIN_USER"], defaults={"is_staff": True, "is_superuser": True, "email": ""})
        if created: u.set_password(env["ADMIN_PASSWORD"]); u.save()
        Group.objects.get_or_create(name="requesters")
        ops, _ = Group.objects.get_or_create(name="operators")
        ops.permissions.add(Permission.objects.get(codename="view_all_requests"), Permission.objects.get(codename="approve_requests"))
        self.stdout.write(f"setup: admin user '{env['ADMIN_USER']}'{' (created)' if created else ''}, groups requesters / operators; "
                          f"secrets in {settings.ENV_FILE}")
