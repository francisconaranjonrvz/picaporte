from django.contrib import admin

from .models import Favorite, Note, Visit


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("company", "priority", "position", "created_at")
    list_filter = ("priority",)
    raw_id_fields = ("company",)


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("company", "status", "visited_on", "next_action", "next_action_on")
    list_filter = ("status",)
    raw_id_fields = ("company",)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("company", "text", "is_status_change", "created_at")
    raw_id_fields = ("company",)
