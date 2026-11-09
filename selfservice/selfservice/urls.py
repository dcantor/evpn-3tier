"""/api/ — the JSON API (docs: /api/docs) and /api/stream (live events); /classic/ — the server-rendered pages (the
first UI, kept as a fallback); /admin/ — Django admin; everything else — the React front end (frontend/, built into
frontend/dist and served from /static/app/), which routes in the browser."""
from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path, re_path

from workflows import spa
from workflows.api import api
from workflows.stream import stream

admin.site.site_header = "Fabric Factory — admin"
admin.site.site_title = "Fabric Factory"
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/stream", stream, name="stream"),
    path("api/", api.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/", auth.PasswordChangeView.as_view(success_url="/classic/"), name="password_change"),
    path("classic/", include("workflows.urls")),
    re_path(r"^(?!api/|admin/|static/|classic/).*$", spa.index, name="spa"),
]
