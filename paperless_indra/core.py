"""Paperless-version-independent pieces shared by the 3.x and 2.x parsers.

Nothing here imports Django or Paperless; callers pass in the Paperless
settings they read (output type, image DPI, ...).
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path

from paperless_indra.config import IndraSettings
from paperless_indra.engine import PageResult
from paperless_indra.textlayer import TextItem, add_text_layer

logger = logging.getLogger("paperless.parsing.indra")

SUPPORTED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# A page with at least this many non-blank characters already has a text
# layer and gets no additional invisible text (avoids duplicate hits).
PAGE_TEXT_MIN_CHARS = 20
# Same threshold Paperless uses to call a PDF "born digital".
PDF_TEXT_MIN_LENGTH = 50

_NULL_ENGINE_PLUGIN = "paperless_indra.ocrmypdf_null"


class ArchiveError(Exception):
    pass


def post_process_text(text: str | None) -> str | None:
    """Paperless' text normalization (collapse spaces, strip, drop NULs)."""
    if not text:
        return None
    text = re.sub(r"([^\S\r\n]+)", " ", text)
    text = re.sub(r"([\n\r]+)([^\S\n\r]+)", "\\1", text)
    text = re.sub(r"([^\S\n\r]+)$", "", text)
    return text.strip().replace("\0", " ") or None


def content_from_pages(pages: list[PageResult], settings: IndraSettings) -> str:
    if settings.content_format == "text":
        text = "\n\n".join(p.text for p in pages if p.text.strip())
        return post_process_text(text) or ""
    return "\n\n".join(p.markdown.strip() for p in pages if p.markdown.strip()).strip()


def log_summary(name: str, pages: list[PageResult]) -> None:
    sources: dict[str, int] = {}
    for p in pages:
        sources[p.source] = sources.get(p.source, 0) + 1
    logger.info(
        "Indra OCR read %s: %d page(s) (%s)",
        name,
        len(pages),
        ", ".join(f"{n} {s}" for s, n in sorted(sources.items())),
    )


def all_pages_failed(pages: list[PageResult]) -> str | None:
    if pages and all(p.source == "error" for p in pages):
        return pages[0].note or "unknown error"
    return None


def pdf_text(pdf: Path) -> str:
    """Existing text of a PDF (pdfminer, ships with OCRmyPDF)."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError:  # pragma: no cover
        return ""
    try:
        return post_process_text(extract_text(str(pdf))) or ""
    except Exception as e:
        logger.warning("Could not read existing PDF text (%s).", e)
        return ""


def pages_with_text(pdf: Path) -> set[int]:
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ImportError:  # pragma: no cover
        return set()
    pages: set[int] = set()
    try:
        for idx, page in enumerate(extract_pages(str(pdf))):
            chars = sum(len("".join(el.get_text().split())) for el in page if isinstance(el, LTTextContainer))
            if chars >= PAGE_TEXT_MIN_CHARS:
                pages.add(idx)
    except Exception as e:
        logger.warning("Could not inspect existing PDF text (%s); assuming none.", e)
        return set()
    return pages


def image_to_pdf(image: Path, tempdir: Path, fallback_dpi: int | None = None) -> Path:
    """Wrap an image into a PDF with sensible DPI (Paperless semantics)."""
    import img2pdf
    from PIL import Image

    src = image
    with Image.open(image) as im:
        dpi = None
        with contextlib.suppress(KeyError, TypeError, IndexError, ValueError):
            dpi = round(im.info["dpi"][0]) or None
        needs_convert = im.mode in ("RGBA", "LA", "P", "PA") or (
            getattr(im, "n_frames", 1) > 1 and im.format == "GIF"
        )
        if needs_convert or im.format not in ("JPEG", "PNG", "TIFF", "JPEG2000"):
            src = tempdir / "image-flat.png"
            im.convert("RGB").save(src, **({"dpi": (dpi, dpi)} if dpi else {}))
        width = im.size[0]
    dpi = dpi or fallback_dpi or max(72, int(width / (21 / 2.54)))  # last resort: A4 width
    out = tempdir / "image.pdf"
    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    try:
        out.write_bytes(img2pdf.convert(str(src), layout_fun=layout, rotation=img2pdf.Rotation.ifvalid))
    except Exception as e:
        raise ArchiveError(f"Could not convert image to PDF: {e}") from e
    return out


def build_archive(
    source_pdf: Path,
    items: list[TextItem],
    tempdir: Path,
    *,
    is_pdf_input: bool,
    output_type: str | None,
    color_conversion_strategy: str | None,
    settings: IndraSettings,
) -> Path:
    """Text layer on OCR'd pages, then PDF/A if requested."""
    skip = pages_with_text(source_pdf) if is_pdf_input else set()
    layered = tempdir / "archive-textlayer.pdf"
    try:
        touched = add_text_layer(source_pdf, items, layered, skip_pages=skip)
    except Exception as e:
        raise ArchiveError(f"Could not write text layer: {e}") from e
    logger.debug("Indra: text layer on %d page(s), %d page(s) already had text", touched, len(skip))
    if settings.pdfa and output_type and "pdfa" in output_type:
        return to_pdfa(layered, tempdir / "archive.pdf", output_type, color_conversion_strategy) or layered
    return layered


def _ocrmypdf_major() -> int:
    import ocrmypdf

    try:
        return int(str(ocrmypdf.__version__).split(".")[0])
    except ValueError:  # pragma: no cover
        return 0


def to_pdfa(src: Path, dst: Path, output_type: str, color_conversion_strategy: str | None) -> Path | None:
    """PDF/A conversion with OCRmyPDF, never running Tesseract.

    OCRmyPDF >= 17 has ``ocr_engine="none"``; on 16.x (Paperless 2.x) the
    same is achieved with a null OCR engine plugin. ``skip_text`` keeps
    pages that have text (all pages Indra read) away from any engine.
    """
    import ocrmypdf

    kwargs: dict = {"output_type": output_type, "skip_text": True, "progress_bar": False}
    if _ocrmypdf_major() >= 17:
        kwargs["ocr_engine"] = "none"
    else:
        kwargs["plugins"] = [_NULL_ENGINE_PLUGIN]
        kwargs["pdf_renderer"] = "hocr"
    if color_conversion_strategy:
        kwargs["color_conversion_strategy"] = str(color_conversion_strategy)
    try:
        ocrmypdf.ocr(src, dst, **kwargs)
        return dst
    except Exception as e:
        logger.warning("PDF/A conversion failed (%s), keeping plain PDF with text layer.", e)
        return None
