import pytest

from apps.llm.prompts import load_prompt


def test_carga_el_prompt_versionado():
    version, text = load_prompt("cv_parse_v1")

    assert version == "cv_parse_v1"
    assert "full_name" in text
    assert "nunca inventes" in text


def test_prompt_inexistente_falla_claro():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_existe_v9")
