"""evpn-3tier self-service: a Django front end where users request workflows on the lab (add a VLAN to the fabric, set up
a load balancer to peer with it, ...). Requests are recorded and validated against the live lab; executing them comes
later. Secrets live outside git: ~/.config/evpn-3tier/selfservice.env (made by `manage.py e3t_setup`)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LAB_DIR = BASE_DIR.parent
ENV_FILE = Path.home() / ".config/evpn-3tier/selfservice.env"


def _env():
    out = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1); out[k.strip()] = v.strip()
    return out


ENV = _env()
SECRET_KEY = os.environ.get("E3T_SS_SECRET_KEY") or ENV.get("SECRET_KEY") or "insecure-dev-key-run-e3t_setup"
DEBUG = os.environ.get("E3T_SS_DEBUG") == "1"
ALLOWED_HOSTS = ["192.168.50.231", "localhost", "127.0.0.1", "10.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["http://192.168.50.231:8120", "http://localhost:8120", "http://127.0.0.1:8120"]

INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                  "django.contrib.messages", "django.contrib.staticfiles", "workflows"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
              "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
              "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "selfservice.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                                                 "django.contrib.messages.context_processors.messages", "workflows.views.lab_context"]}}]
WSGI_APPLICATION = "selfservice.wsgi.application"
ASGI_APPLICATION = "selfservice.asgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "data" / "db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
                            {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}]
LANGUAGE_CODE = "en-us"
TIME_ZONE = (Path("/etc/timezone").read_text().strip() if Path("/etc/timezone").exists() else "UTC")
USE_I18N, USE_TZ = False, True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "data" / "static"
STATICFILES_DIRS = [("app", BASE_DIR / "frontend" / "dist")] if (BASE_DIR / "frontend" / "dist").exists() else []   # the React build (Vite base /static/app/)
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL, LOGIN_REDIRECT_URL, LOGOUT_REDIRECT_URL = "login", "dashboard", "login"   # the classic pages'
SESSION_COOKIE_NAME = "e3t_selfservice"          # the Lab Hub's cookie on the same host is another name
SESSION_COOKIE_AGE = 12 * 3600
LOGGING = {"version": 1, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": "INFO"}}
SECURE_CROSS_ORIGIN_OPENER_POLICY = None   # plain HTTP in the lab: browsers ignore the header and log a warning
