from django.contrib import admin

from .models import Route, RouteStop


class RouteStopInline(admin.TabularInline):
    model = RouteStop
    raw_id_fields = ("company",)
    extra = 0


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("date", "slot", "zone", "created_at")
    list_filter = ("slot", "zone")
    inlines = [RouteStopInline]
