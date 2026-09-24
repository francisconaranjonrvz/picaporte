from django.contrib import admin

from .dedupe import company_domain
from .models import Company, FetchCache, Source, SourceRecord

# Campos que el descubrimiento rellena (merge_into, zona y reactivación).
MANUAL_FIELDS = {
    "name",
    "category",
    "zone",
    "address",
    "postcode",
    "city",
    "lat",
    "lng",
    "website",
    "domain",
    "phone",
    "email",
    "opening_hours",
    "rating",
    "rating_count",
    "is_active",
}


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

    def save_model(self, request, obj, form, change):
        """Lo editado a mano pasa a ser de la fuente 'manual' y el descubrimiento no lo pisa."""
        changed = set(form.changed_data) & MANUAL_FIELDS
        if "website" in changed and "domain" not in changed:
            obj.domain = company_domain(obj.website)
            changed.add("domain")
        if changed:
            sources = dict(obj.field_sources or {})
            sources.update(dict.fromkeys(changed, Source.MANUAL.value))
            obj.field_sources = sources
        super().save_model(request, obj, form, change)


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
