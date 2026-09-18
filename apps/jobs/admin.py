from django.contrib import admin

from .models import JobRun


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "status", "trigger", "duration_seconds", "github_run_id")
    list_filter = ("kind", "status", "trigger")
    readonly_fields = [f.name for f in JobRun._meta.fields]
