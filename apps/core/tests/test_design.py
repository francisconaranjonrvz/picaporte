import pytest

from apps.core.design import AA_TEXT, PAIRS, TOKENS, contrast_ratio, relative_luminance


def test_extremos_de_luminancia():
    assert relative_luminance("#000000") == 0
    assert relative_luminance("#FFFFFF") == pytest.approx(1)
    assert contrast_ratio("#000", "#fff") == 21


def test_ratio_es_simetrico():
    assert contrast_ratio("#1E293B", "#F5F9FF") == contrast_ratio("#F5F9FF", "#1E293B")


@pytest.mark.parametrize("pair", PAIRS, ids=[p.uso for p in PAIRS])
def test_cada_par_de_la_ui_cumple_wcag_aa(pair):
    assert pair.passes, f"{pair.fg}/{pair.bg} = {pair.ratio}:1 < {pair.minimum}:1 ({pair.uso})"


def test_el_azul_de_accion_no_se_usa_como_texto_normal_sobre_blanco():
    # Documenta la decisión: #5B8DEF solo vale para UI/texto grande (>= 3:1), no para texto normal.
    assert contrast_ratio(TOKENS["action"], TOKENS["surface"]) < AA_TEXT
    assert contrast_ratio(TOKENS["white"], TOKENS["action-fill"]) >= AA_TEXT
