from django.contrib import admin

from .models import RequestEvent, WorkflowRequest


class EventInline(admin.TabularInline):
    model = RequestEvent
    extra = 0
    readonly_fields = ("at", "actor", "kind", "message")
    can_delete = False


@admin.register(WorkflowRequest)
class WorkflowRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "workflow", "status", "requested_by", "created")
    list_filter = ("status", "workflow")
    search_fields = ("title", "reason", "requested_by__username")
    readonly_fields = ("created", "updated")
    inlines = [EventInline]
