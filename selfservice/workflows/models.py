"""A workflow request and its history. Executing requests (pipelines, Nautobot) comes later: the statuses for it exist."""
from django.conf import settings
from django.db import models
from django.urls import reverse


class WorkflowRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"           # an operator accepted it: queued for the worker
        RUNNING = "running", "Running"              # the worker carries it out (workflows/executors.py)
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    OPEN = (Status.SUBMITTED, Status.APPROVED)       # a requester may still cancel these (approved: until the worker picks it up)

    workflow = models.SlugField(max_length=40, help_text="catalog slug (workflows/catalog.py)")
    title = models.CharField(max_length=200)
    parameters = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workflow_requests")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_requests")
    decided_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)          # what execution produced (pipeline runs, links)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        permissions = [("view_all_requests", "Can see every user's requests"),
                       ("approve_requests", "Can approve or reject requests (approved ones are carried out)")]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    def get_absolute_url(self):
        return reverse("request_detail", args=[self.pk])

    def log(self, actor, kind, message=""):
        return self.events.create(actor=actor, kind=kind, message=message)


class RequestEvent(models.Model):
    request = models.ForeignKey(WorkflowRequest, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=30)          # submitted, cancelled, comment, (later) approved, run-started, ...
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["at"]
