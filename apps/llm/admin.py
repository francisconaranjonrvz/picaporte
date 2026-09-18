from django.contrib import admin

from .models import LLMCall


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "purpose",
        "model",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    )
    list_filter = ("purpose", "model", "prompt_version")
    readonly_fields = [f.name for f in LLMCall._meta.fields]
    ordering = ("-created_at",)
