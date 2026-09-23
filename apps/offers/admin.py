from django.contrib import admin

from .models import JobOffer


@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = ("title", "company_name", "company", "match", "portal", "published_on")
    list_filter = ("match", "source", "portal")
    search_fields = ("title", "company_name", "url")
    raw_id_fields = ("company",)
