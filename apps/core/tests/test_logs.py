import json
import logging

from config.logs import JsonFormatter


def test_formatea_una_linea_json():
    record = logging.LogRecord(
        "picaporte", logging.WARNING, __file__, 1, "hola %s", ("mundo",), None
    )

    line = JsonFormatter().format(record)

    payload = json.loads(line)
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "picaporte"
    assert payload["msg"] == "hola mundo"
    assert "exc" not in payload


def test_incluye_traza_si_hay_excepcion():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "fallo", (), sys.exc_info())

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exc"]
