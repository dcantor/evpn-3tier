from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path

admin.site.site_header = "evpn-3tier self-service — admin"
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/", auth.PasswordChangeView.as_view(success_url="/"), name="password_change"),
    path("", include("workflows.urls")),
]
