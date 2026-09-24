from django.contrib import admin

from .models import JobOffer


@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "company_name", "company", "match", "published_on")
    list_filter = ("user", "match", "source", "portal")
    search_fields = ("title", "company_name", "url")
    raw_id_fields = ("company",)
