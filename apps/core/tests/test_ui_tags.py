from django.template import Context, Template


def render(source: str, **context) -> str:
    return Template("{% load ui %}" + source).render(Context(context))


def test_card_envuelve_el_contenido():
    html = render('{% card cls="extra" %}<p>hola</p>{% endcard %}')

    assert html == '<div class="card extra"><p>hola</p></div>'


def test_btn_por_defecto_es_button_primario():
    html = render("{% btn %}Guardar{% endbtn %}")

    assert html.startswith("<button ")
    assert 'type="button"' in html
    assert "bg-action-fill" in html
    assert "min-h-11" in html
    assert html.endswith(">Guardar</button>")


def test_btn_con_href_es_enlace():
    html = render('{% btn href="/x/" variant="secondary" size="sm" %}Ir{% endbtn %}')

    assert html.startswith("<a ")
    assert 'href="/x/"' in html
    assert "bg-primary" in html
    assert "min-h-9" in html


def test_btn_escapa_atributos_pero_no_el_contenido_renderizado():
    html = render('{% btn hx_get=url %}{% icon "check" %} Ok{% endbtn %}', url='/a?b=1&c="x"')

    assert 'hx-get="/a?b=1&amp;c=&quot;x&quot;"' in html
    assert '<use href="#i-check"></use>' in html  # el contenido anidado no se re-escapa


def test_btn_deshabilitado():
    assert 'disabled="disabled"' in render("{% btn disabled=True %}No{% endbtn %}")
    assert 'aria-disabled="true"' in render('{% btn href="/" disabled=True %}No{% endbtn %}')


def test_icon_usa_el_sprite():
    html = render('{% icon "heart" "size-6" %}')

    assert html == (
        '<svg class="size-6" aria-hidden="true" focusable="false"><use href="#i-heart"></use></svg>'
    )


def test_http_url_solo_deja_pasar_http_y_https():
    source = "[{{ url|http_url }}]"
    assert render(source, url="https://sol.com") == "[https://sol.com]"
    assert render(source, url="Http://sol.com") == "[Http://sol.com]"
    assert render(source, url="www.sol.com") == "[]"
    assert render(source, url="javascript:alert(1)") == "[]"
    assert render(source, url=None) == "[]"
