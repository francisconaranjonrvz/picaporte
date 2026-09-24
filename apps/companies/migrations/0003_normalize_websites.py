"""Normaliza las webs ya guardadas: solo http(s) (fuera 'javascript:') y con esquema.

También vacía el dominio de los perfiles en hosts compartidos (facebook.com...).
"""

from django.db import migrations


def normalize_websites(apps, schema_editor):
    from apps.companies.dedupe import company_domain, normalize_website

    Company = apps.get_model("companies", "Company")
    changed = []
    for company in Company.objects.exclude(website="", domain="").only("website", "domain"):
        website = normalize_website(company.website)
        if website:
            domain = company_domain(website)
        else:  # web descartada: su dominio tampoco vale
            domain = "" if company.website else company_domain(company.domain)
        if (website, domain) != (company.website, company.domain):
            company.website, company.domain = website, domain
            changed.append(company)
    Company.objects.bulk_update(changed, ["website", "domain"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0002_fuentes_sin_directorios"),
    ]

    operations = [
        migrations.RunPython(normalize_websites, migrations.RunPython.noop),
    ]
