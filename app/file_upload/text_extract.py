"""Extract plain text from an uploaded document for anchor enrichment.

Enrichment (enrichment.py) needs the doc's text to pull decisions out of it, but
uploads arrive in whatever format the engineer has — Markdown, plain text, PDF,
Word. The full file always lands in Backboard's RAG store regardless; this module
only produces the best-effort *text* the claim extractor reads. A format we can't
read yields "" and enrichment simply writes no anchor memories for it (the doc is
still searchable in RAG).

Format is chosen by extension: PDF via ``pypdf``, .docx via ``python-docx``,
everything else (``.md``, ``.txt``, code, unknown) by a tolerant UTF-8 decode —
the same behavior legacy text uploads already had. Every path is best-effort: a
parser error, an encrypted PDF, or a binary blob degrades to "" rather than
raising, because a failed extraction must never break an upload.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Cap the bytes/pages we pull from any one file — the extractor caps again at
# MAX_DOC_CHARS, but bound the read so a huge PDF can't blow up memory.
_MAX_TEXT_BYTES = 400_000
_MAX_PDF_PAGES = 200


def _extract_txt(path: str) -> str:
    """Tolerant UTF-8 decode — Markdown, plain text, code, anything text-like."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_TEXT_BYTES)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="ignore")


def _extract_pdf(path: str) -> str:
    """Concatenate the text layer of each page (up to a page cap). Scanned PDFs
    with no text layer yield "" — we don't OCR."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = reader.pages[:_MAX_PDF_PAGES]
        return "\n".join((page.extract_text() or "") for page in pages)
    except Exception:  # noqa: BLE001 — encrypted/corrupt PDF → no text, not an error
        logger.exception("could not extract text from PDF %s", path)
        return ""


def _extract_docx(path: str) -> str:
    """Paragraph text of a .docx. The legacy .doc binary format is not supported
    (python-docx reads only Open-XML) and falls through to "" here."""
    try:
        import docx

        document = docx.Document(path)
        return "\n".join(p.text for p in document.paragraphs)
    except Exception:  # noqa: BLE001 — old .doc / corrupt file → no text
        logger.exception("could not extract text from DOCX %s", path)
        return ""


def extract_document_text(path: str, filename: str | None) -> str:
    """Best-effort plain text of an uploaded document, dispatched by extension.

    Returns "" for anything unreadable (unknown/binary format, parser failure),
    so callers treat "no text" and "extraction failed" the same way — enrichment
    just writes no anchor memories."""
    suffix = Path(filename or path).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    return _extract_txt(path)
