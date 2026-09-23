"""Lectura de ofertas: `scan-history.tsv` de career-ops, CSV genérico y JSON.

Todo se normaliza a `OfferRow`. Formatos admitidos:

- **career-ops** (`data/scan-history.tsv`): columnas `url, first_seen, portal, title,
  company, status, location` y opcionales detrás (la 9 es la fecha de publicación).
  Solo se importan las filas `added`; las `skipped_*` (filtradas por título,
  duplicadas, caducadas) no son ofertas que interesen.
- **CSV** con cabecera, en inglés o en español: `title/titulo/puesto`,
  `company/empresa`, `url/enlace`, `portal`, `date/fecha`, `score/puntuacion`,
  `location/ubicacion`. Separador `,` o `;` (Excel en español).
- **JSON**: una lista de objetos con las mismas claves, o `{"offers": [...]}`.
"""

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date

MAX_BYTES = 2 * 1024 * 1024
CAREER_OPS_HEADER = ["url", "first_seen", "portal", "title", "company", "status", "location"]
ALIASES = {
    "title": ("title", "titulo", "título", "puesto", "role", "position"),
    "company": ("company", "empresa", "company_name"),
    "url": ("url", "enlace", "link"),
    "portal": ("portal", "source", "fuente"),
    "date": ("date", "fecha", "posted", "published", "first_seen"),
    "score": ("score", "puntuacion", "puntuación", "nota"),
    "location": ("location", "ubicacion", "ubicación", "ciudad", "city"),
}


class ImportFormatError(ValueError):
    pass


@dataclass
class OfferRow:
    title: str
    company: str
    url: str
    portal: str = ""
    location: str = ""
    published_on: date | None = None
    score: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    rows: list[OfferRow]
    source: str
    skipped: int = 0  # filas descartadas (estado skipped_* o sin datos mínimos)


def _date(value: str | None) -> date | None:
    value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _score(value) -> float | None:
    try:
        return (
            float(str(value).replace(",", ".").split("/")[0]) if value not in (None, "") else None
        )
    except ValueError:
        return None


def _decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ImportFormatError("El archivo supera 2 MB.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFormatError("No se reconoce la codificación del archivo.")


def _row(title, company, url, **extra) -> OfferRow | None:
    title, company, url = (title or "").strip(), (company or "").strip(), (url or "").strip()
    if not (title and company and url.startswith(("http://", "https://"))):
        return None
    return OfferRow(title=title[:300], company=company[:200], url=url[:600], **extra)


def parse_career_ops(text: str) -> ParseResult:
    rows, skipped = [], 0
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    next(reader, None)  # cabecera
    for cells in reader:
        if not any(cells):
            continue
        cells += [""] * (9 - len(cells))
        url, first_seen, portal, title, company, status, location = cells[:7]
        if status.strip() != "added":
            skipped += 1
            continue
        row = _row(
            title,
            company,
            url,
            portal=portal.strip()[:120],
            location=location.strip()[:200],
            published_on=_date(cells[8]) or _date(first_seen),
            raw={"first_seen": first_seen, "status": status, "posted": cells[8]},
        )
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return ParseResult(rows, "career-ops", skipped)


def _pick(record: dict, key: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for alias in ALIASES[key]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return str(lowered[alias])
    return ""


def _from_records(records: list[dict], source: str) -> ParseResult:
    rows, skipped = [], 0
    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue
        row = _row(
            _pick(record, "title"),
            _pick(record, "company"),
            _pick(record, "url"),
            portal=_pick(record, "portal")[:120],
            location=_pick(record, "location")[:200],
            published_on=_date(_pick(record, "date")),
            score=_score(_pick(record, "score")),
            raw={k: str(v)[:200] for k, v in record.items()},
        )
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return ParseResult(rows, source, skipped)


def parse(filename: str, data: bytes) -> ParseResult:
    """Detecta el formato por contenido (y extensión) y devuelve las filas."""
    text = _decode(data).strip()
    if not text:
        raise ImportFormatError("El archivo está vacío.")
    first_line = text.splitlines()[0].strip().lower()
    if first_line.split("\t")[:7] == CAREER_OPS_HEADER:
        return parse_career_ops(text)
    if filename.lower().endswith(".json") or text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"JSON no válido: {exc.msg} (línea {exc.lineno}).") from exc
        records = payload.get("offers") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ImportFormatError('El JSON debe ser una lista de ofertas o {"offers": [...]}.')
        return _from_records(records, "json")
    delimiter = (
        "\t"
        if "\t" in first_line
        else (";" if first_line.count(";") > first_line.count(",") else ",")
    )
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    if not any(alias in headers for alias in ALIASES["url"]):
        raise ImportFormatError("El CSV necesita al menos las columnas título, empresa y url.")
    return _from_records(list(reader), "csv")
