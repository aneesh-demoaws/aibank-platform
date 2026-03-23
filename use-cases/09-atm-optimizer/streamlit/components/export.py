"""
CSV and PDF export functionality for simulation results.

Provides download buttons for exporting the latest agent response data
as CSV (tabular data) or a simple PDF report.

Validates: Requirements 8.5, 10.5 (data_export is Admin-only feature)
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any, Optional

import streamlit as st

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _build_csv(data: dict[str, Any]) -> str:
    """Convert agent response data into a CSV string.

    Handles both flat dicts and lists-of-dicts (tabular results).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    # If the response contains a "results" list, export as table
    results = data.get("results", data.get("data", []))
    if isinstance(results, list) and results:
        if isinstance(results[0], dict):
            headers = list(results[0].keys())
            writer.writerow(headers)
            for row in results:
                writer.writerow([row.get(h, "") for h in headers])
            return buf.getvalue()

    # Fallback: export top-level key/value pairs
    writer.writerow(["Field", "Value"])
    for key, value in data.items():
        if key in ("response", "error", "session_id", "tool_calls"):
            continue
        writer.writerow([key, value])

    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF export (lightweight — no heavy dependencies)
# ---------------------------------------------------------------------------

def _build_pdf_bytes(data: dict[str, Any], title: str) -> bytes:
    """Build a minimal PDF report from agent response data.

    Uses a simple text-based PDF structure to avoid requiring
    heavy libraries like reportlab or fpdf in the base install.
    """
    lines: list[str] = []
    lines.append(title)
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    response_text = data.get("response", "")
    if response_text:
        lines.append("Analysis Summary")
        lines.append("-" * 40)
        # Wrap long lines
        for paragraph in response_text.split("\n"):
            lines.append(paragraph)
        lines.append("")

    results = data.get("results", data.get("data", []))
    if isinstance(results, list) and results:
        lines.append("Detailed Results")
        lines.append("-" * 40)
        for i, item in enumerate(results, 1):
            if isinstance(item, dict):
                lines.append(f"  Record {i}:")
                for k, v in item.items():
                    lines.append(f"    {k}: {v}")
            else:
                lines.append(f"  {item}")
        lines.append("")

    # Build a minimal valid PDF
    text_content = "\n".join(lines)
    return _text_to_pdf(text_content, title)


def _text_to_pdf(text: str, title: str) -> bytes:
    """Create a minimal PDF from plain text.

    This produces a valid PDF 1.4 document without external libraries.
    """
    # Escape special PDF characters
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    # Split into lines and build PDF text operators
    pdf_lines = []
    y = 750
    for line in safe.split("\n"):
        if y < 50:
            break  # Simple single-page limit
        pdf_lines.append(f"BT /F1 10 Tf 50 {y} Td ({line}) Tj ET")
        y -= 14

    stream = "\n".join(pdf_lines)
    stream_bytes = stream.encode("latin-1", errors="replace")

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream_bytes)).encode() + b">>\n"
        b"stream\n" + stream_bytes + b"\nendstream\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000306 00000 n \n"
        b"0000000260 00000 n \n"
        b"trailer<</Size 6/Root 1 0 R>>\n"
        b"startxref\n0\n%%EOF"
    )
    return pdf


# ---------------------------------------------------------------------------
# Public component
# ---------------------------------------------------------------------------

def render_export(data: Optional[dict[str, Any]] = None) -> None:
    """Render export buttons for the latest simulation results.

    Parameters
    ----------
    data:
        Agent response dict. Falls back to ``st.session_state["last_response_data"]``.
    """
    export_data = data or st.session_state.get("last_response_data")

    if not export_data or export_data.get("error"):
        st.info("Run a query first to enable data export.")
        return

    st.subheader("📥 Export Results")

    col1, col2 = st.columns(2)

    with col1:
        csv_content = _build_csv(export_data)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="⬇️ Download CSV",
            data=csv_content,
            file_name=f"atm_analysis_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col2:
        pdf_bytes = _build_pdf_bytes(
            export_data,
            title="NeoBank ATM Profitability Analysis Report",
        )
        st.download_button(
            label="⬇️ Download PDF",
            data=pdf_bytes,
            file_name=f"atm_analysis_{timestamp}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
