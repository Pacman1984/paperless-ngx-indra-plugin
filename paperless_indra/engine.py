"""Run Indra OCR in-process and map its results onto the displayed PDF pages.

Indra renders each displayed page (pdfium applies CropBox and /Rotate) at
``Converter.page_scale(page)`` pixels per point. If the scan itself lies
sideways or upside down it turns the image upright first
(``np.rot90(gray, -k)``) and reports line boxes in that turned frame with
``Page.rotation = 90 * k``. :func:`map_box` undoes the turn and normalizes
the box to fractions of the displayed page, which is what the text layer
expects.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperless_indra.config import IndraSettings
from paperless_indra.textlayer import TextItem

logger = logging.getLogger("paperless.parsing.indra")

# Lines read from a PDF text layer get confidence exactly 1.0 from Indra.
_TEXT_LAYER_CONF = 1.0

_lock = threading.Lock()
_converters: dict[tuple, Any] = {}


class IndraError(Exception):
    """Indra could not process the document."""


@dataclass(slots=True)
class PageResult:
    page_idx: int
    source: str  # ocr | text | hybrid | error
    markdown: str
    text: str
    items: list[TextItem] = field(default_factory=list)  # OCR'd lines only
    note: str = ""


def get_converter(settings: IndraSettings):
    """One Indra converter per worker process (models load once, ~1 s)."""
    key = settings.converter_key()
    with _lock:
        conv = _converters.get(key)
        if conv is None:
            import indra_ocr

            logger.info(
                "Loading Indra OCR %s (threads=%d, dpi=%g, mode=%s)",
                getattr(indra_ocr, "__version__", "?"),
                settings.threads,
                settings.dpi,
                settings.mode,
            )
            try:
                conv = indra_ocr.load(
                    path=settings.model_dir,
                    threads=settings.threads,
                    dpi=settings.dpi,
                    mode=settings.mode,
                    furniture=settings.keep_furniture,
                    math=settings.math,
                )
            except Exception as e:
                raise IndraError(f"could not load the Indra models: {e}") from e
            _converters[key] = conv
        return conv


def map_box(
    box: list[float],
    rotation: int,
    page_px: tuple[float, float],
) -> tuple[float, float, float, float] | None:
    """Indra box (pixels, turned frame) -> fractions of the displayed page.

    ``page_px`` is the displayed page size in render pixels (before Indra's
    own turn). Returns None for degenerate boxes.
    """
    W, H = page_px
    if W <= 0 or H <= 0:
        return None
    x0, y0, x1, y1 = (float(v) for v in box)
    k = (int(rotation) // 90) % 4
    # The turned image is rot90(page, -k): undo it corner by corner.
    if k == 1:
        xs, ys = (y0, y1), (H - x0, H - x1)
    elif k == 2:
        xs, ys = (W - x0, W - x1), (H - y0, H - y1)
    elif k == 3:
        xs, ys = (W - y0, W - y1), (x0, x1)
    else:
        xs, ys = (x0, x1), (y0, y1)
    fx0, fx1 = sorted(min(max(v / W, 0.0), 1.0) for v in xs)
    fy0, fy1 = sorted(min(max(v / H, 0.0), 1.0) for v in ys)
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    return (fx0, fy0, fx1, fy1)


def _page_pixels(conv, pdf_page) -> tuple[float, float]:
    w, h = pdf_page.get_size()  # displayed size in points (CropBox, /Rotate applied)
    k = conv.page_scale(pdf_page)
    return math.ceil(w * k), math.ceil(h * k)


def run(pdf: Path, settings: IndraSettings) -> list[PageResult]:
    """OCR every page of ``pdf`` and return per-page results."""
    import indra_ocr
    import pypdfium2 as pdfium

    conv = get_converter(settings)
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as e:
        raise IndraError(f"cannot open {pdf.name}: {e}") from e
    try:
        sizes = [_page_pixels(conv, doc[i]) for i in range(len(doc))]
    finally:
        doc.close()

    results: list[PageResult] = []
    try:
        for idx, page in conv.convert(str(pdf)):
            items: list[TextItem] = []
            if page.source in ("ocr", "hybrid"):
                turn = (int(page.rotation) // 90) % 4
                for line in page.lines:
                    # On hybrid pages only the lines Indra had to OCR need a layer.
                    if page.source == "hybrid" and line.conf >= _TEXT_LAYER_CONF:
                        continue
                    bbox = map_box(line.box, page.rotation, sizes[idx])
                    if bbox and line.text.strip():
                        items.append(TextItem(idx, bbox, line.text.strip(), turn))
            if page.source == "error":
                logger.warning("Indra could not read page %d of %s: %s", idx + 1, pdf.name, page.note)
            results.append(
                PageResult(
                    page_idx=idx,
                    source=page.source,
                    markdown=page.markdown or "",
                    text=page.text,
                    items=items,
                    note=page.note,
                ),
            )
    except indra_ocr.InputError as e:
        raise IndraError(str(e)) from e
    return results
