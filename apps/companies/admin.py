from django.contrib import admin

from .models import Company, FetchCache, SourceRecord


class SourceRecordInline(admin.TabularInline):
    model = SourceRecord
    fields = ("source", "external_id", "name", "fetched_at")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "zone",
        "confidence_score",
        "website",
        "phone",
        "last_seen_at",
    )
    list_filter = ("category", "zone", "is_active")
    search_fields = ("name", "domain", "address")
    readonly_fields = ("field_sources", "first_seen_at", "last_seen_at", "confidence_score")
    inlines = [SourceRecordInline]


@admin.register(SourceRecord)
class SourceRecordAdmin(admin.ModelAdmin):
    list_display = ("source", "external_id", "name", "company", "fetched_at")
    list_filter = ("source",)
    search_fields = ("name", "external_id")
    readonly_fields = ("payload",)


@admin.register(FetchCache)
class FetchCacheAdmin(admin.ModelAdmin):
    list_display = ("url", "status_code", "fetched_at", "expires_at")
    readonly_fields = ("key", "url", "status_code", "fetched_at", "expires_at", "body")
