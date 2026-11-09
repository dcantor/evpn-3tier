from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("catalog/", login_required(views.catalog), name="catalog"),
    path("new/<slug:slug>/", views.new_request, name="new_request"),
    path("requests/", views.all_requests, name="all_requests"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/cancel/", views.cancel_request, name="cancel_request"),
    path("requests/<int:pk>/<str:decision>/", views.decide_request, name="decide_request"),
]
