import pytest

from apps.enrichment.ranking import breakdown_rows, clamp_breakdown, verdict
from apps.enrichment.schemas import CompanyScore


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (100, "Ve primero"),
        (75, "Ve primero"),
        (74, "Merece la pena"),
        (60, "Merece la pena"),
        (59, "Si pasas cerca"),
        (40, "Si pasas cerca"),
        (39, "Baja prioridad"),
        (0, "Baja prioridad"),
    ],
)
def test_veredicto_por_bandas(score, label):
    assert verdict(score).label == label


def test_sin_nota_no_hay_veredicto():
    assert verdict(None) is None


def test_la_nota_es_la_suma_de_criterios_acotados():
    score = CompanyScore(
        company_id=1,
        sector="35",  # texto
        junior=25.4,  # se pasa del máximo (20)
        preferences=-3,  # negativo
        languages="n/d",  # basura
        clarity=7.6,
        reason="motivo",
        hook="Hola.",
    )
    assert score.breakdown == {
        "sector": 35,
        "junior": 20,
        "preferences": 0,
        "languages": 0,
        "clarity": 8,
    }
    assert score.fit_score == 63


def test_filas_del_desglose_para_la_ficha():
    rows = breakdown_rows(clamp_breakdown({"sector": 30, "junior": 10}))
    assert rows[0] == {"label": "Sector y servicios afines", "points": 30, "max": 40, "pct": 75}
    assert [r["points"] for r in rows] == [30, 10, 0, 0, 0]
    assert breakdown_rows({}) == []  # puntuada con el prompt v1: sin desglose
