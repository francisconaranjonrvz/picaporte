from django.contrib import admin

from .models import CompanyPage, Enrichment


@admin.register(Enrichment)
class EnrichmentAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "fit_score",
        "crawl_status",
        "size_estimate",
        "requires_catalan",
        "scored_at",
    )
    list_filter = ("crawl_status", "size_estimate", "requires_catalan", "is_company_site")
    search_fields = ("company__name", "summary", "services")
    raw_id_fields = ("company",)
    readonly_fields = ("updated_at",)


@admin.register(CompanyPage)
class CompanyPageAdmin(admin.ModelAdmin):
    list_display = ("company", "kind", "url", "status_code", "lang", "fetched_at")
    list_filter = ("kind", "lang")
    search_fields = ("company__name", "url")
    raw_id_fields = ("company",)
