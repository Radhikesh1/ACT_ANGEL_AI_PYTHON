from io import BytesIO

from pypdf import PdfReader


def extract_pdf_text(data: bytes) -> str:
    """Extract plain text from a PDF's pages, joined with blank lines."""
    reader = PdfReader(BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p for p in pages if p.strip())
