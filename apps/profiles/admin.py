from django.contrib import admin

from .models import CVDocument, Profile


class CVInline(admin.StackedInline):
    model = CVDocument
    readonly_fields = ("filename", "size", "sha256", "uploaded_at")
    can_delete = True
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "headline", "parsed_at", "updated_at")
    filter_horizontal = ("categories", "zones")
    readonly_fields = ("parsed_at", "parsed_model", "parsed_prompt_version", "updated_at")
    inlines = [CVInline]
