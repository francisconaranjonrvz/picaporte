"""Lo que cada usuario ve de una empresa: su encaje, su estado y si es su favorita.

El catálogo de empresas (y lo leído de sus webs) es común; la puntuación y el
seguimiento son de cada usuario (ADR 0015). `for_user` añade a un queryset de
empresas las anotaciones para filtrar y ordenar en SQL (`fit`, `visit_status`,
`is_favorite`) y precarga las filas del usuario, que las plantillas leen con
`company.score`, `company.visit` y `company.favorite`.
"""

from django.db.models import Exists, OuterRef, Prefetch, QuerySet, Subquery

from apps.enrichment.models import FitScore
from apps.tracking.models import Favorite, Visit

from .models import Company


def for_user(qs: QuerySet[Company], user) -> QuerySet[Company]:
    scores = FitScore.objects.filter(user=user)
    visits = Visit.objects.filter(user=user)
    favorites = Favorite.objects.filter(user=user)
    return qs.annotate(
        fit=Subquery(scores.filter(company=OuterRef("pk")).values("fit_score")[:1]),
        visit_status=Subquery(visits.filter(company=OuterRef("pk")).values("status")[:1]),
        is_favorite=Exists(favorites.filter(company=OuterRef("pk"))),
    ).prefetch_related(
        Prefetch("fit_scores", queryset=scores, to_attr="my_scores"),
        Prefetch("visits", queryset=visits, to_attr="my_visits"),
        Prefetch("favorites", queryset=favorites, to_attr="my_favorites"),
    )


def attach(company: Company, user) -> Company:
    """Lo mismo para una empresa suelta (ficha, respuestas HTMX)."""
    company.my_scores = list(FitScore.objects.filter(user=user, company=company))
    company.my_visits = list(Visit.objects.filter(user=user, company=company))
    company.my_favorites = list(Favorite.objects.filter(user=user, company=company))
    return company
