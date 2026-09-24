from django.contrib import admin

from .models import Favorite, Note, Visit


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "priority", "position", "created_at")
    list_filter = ("user", "priority")
    raw_id_fields = ("company",)


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "status", "visited_on", "next_action", "next_action_on")
    list_filter = ("user", "status")
    raw_id_fields = ("company",)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "text", "is_status_change", "created_at")
    list_filter = ("user",)
    raw_id_fields = ("company",)
