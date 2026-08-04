use pyo3::prelude::*;

const MAX_LINES_PER_PAGE: usize = 65;

fn escape_pdf_text(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('(', "\\(")
        .replace(')', "\\)")
        .replace('\r', " ")
        .replace('\n', " ")
}

fn latin1_safe_line(value: &str) -> String {
    value
        .chars()
        .map(|ch| if (ch as u32) <= 0xFF { ch } else { '?' })
        .collect()
}

fn render_single_page_pdf_impl(text: &str) -> Vec<u8> {
    let normalized_text = text.replace("\r\n", "\n").replace('\r', "\n");
    let normalized_lines: Vec<&str> = normalized_text.split('\n').collect();

    let lines: Vec<String> = if normalized_lines.is_empty() {
        vec![String::new()]
    } else {
        normalized_lines
            .into_iter()
            .take(MAX_LINES_PER_PAGE)
            .map(latin1_safe_line)
            .collect()
    };

    let mut commands = vec![
        "BT".to_string(),
        "/F1 10 Tf".to_string(),
        "36 806 Td".to_string(),
    ];
    for (idx, line) in lines.iter().enumerate() {
        commands.push(format!("({}) Tj", escape_pdf_text(line)));
        if idx + 1 < lines.len() {
            commands.push("0 -12 Td".to_string());
        }
    }
    commands.push("ET".to_string());
    let stream = commands.join("\n").into_bytes();

    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".to_vec(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
        [
            format!("<< /Length {} >>\nstream\n", stream.len()).into_bytes(),
            stream,
            b"\nendstream".to_vec(),
        ]
        .concat(),
    ];

    let mut payload = b"%PDF-1.4\n".to_vec();
    let mut offsets = vec![0_usize];
    for (obj_index, obj_payload) in objects.iter().enumerate() {
        offsets.push(payload.len());
        payload.extend_from_slice(format!("{} 0 obj\n", obj_index + 1).as_bytes());
        payload.extend_from_slice(obj_payload);
        payload.extend_from_slice(b"\nendobj\n");
    }

    let startxref = payload.len();
    payload.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    payload.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets.iter().skip(1) {
        payload.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    payload.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n",
            objects.len() + 1,
            startxref
        )
        .as_bytes(),
    );

    payload
}

#[pyfunction]
pub(crate) fn render_single_page_pdf(py: Python<'_>, text: &str) -> PyResult<Vec<u8>> {
    let owned_text = text.to_owned();
    Ok(py.allow_threads(move || render_single_page_pdf_impl(&owned_text)))
}
