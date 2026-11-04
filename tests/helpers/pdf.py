"""Independent single-page PDF fixtures and native-renderer reference; not clinical rendering."""


def escape_pdf_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render_single_page_pdf(text: str) -> bytes:
    normalized_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    max_lines = 65
    lines = normalized_lines[:max_lines] if normalized_lines else [""]
    commands = ["BT", "/F1 10 Tf", "36 806 Td"]
    for idx, raw_line in enumerate(lines):
        safe_line = raw_line.encode("latin-1", "replace").decode("latin-1")
        commands.append(f"({escape_pdf_text(safe_line)}) Tj")
        if idx < len(lines) - 1:
            commands.append("0 -12 Td")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]

    payload = b"%PDF-1.4\n"
    offsets = [0]
    for obj_index, obj_payload in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{obj_index} 0 obj\n".encode("ascii")
        payload += obj_payload
        payload += b"\nendobj\n"

    startxref = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    payload += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode("ascii")
    payload += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode(
        "ascii"
    )
    return payload
