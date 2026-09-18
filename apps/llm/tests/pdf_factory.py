"""Genera PDFs mínimos válidos para tests (sin dependencias externas)."""


def make_text_pdf(*lines: str) -> bytes:
    """PDF de una página con las líneas dadas en Helvetica (texto extraíble con pypdf)."""
    ops = "".join(
        f"BT /F1 12 Tf 72 {760 - 18 * i} Td ({line.replace('(', '').replace(')', '')}) Tj ET\n"
        for i, line in enumerate(lines)
    ).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return pdf


def make_blank_pdf() -> bytes:
    """PDF válido sin texto (como un escaneo sin OCR)."""
    return make_text_pdf()
