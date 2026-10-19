"""Write an invisible, searchable text layer onto PDF pages with pikepdf.

The text is drawn in render mode 3 (invisible) with a glyphless CID font
(Identity-H, CID == Unicode BMP code point, identity ToUnicode CMap), the same
technique Tesseract and OCRmyPDF use. Every glyph is 500/1000 em wide and the
horizontal scaling (``Tz``) stretches each line to its box, so selection and
search highlights line up with the scanned text.

Boxes arrive as fractions of the *displayed* page (origin top-left, y down),
which is what a viewer shows after applying the page's CropBox and /Rotate.
``TextItem.turn`` says how the text itself runs on that displayed page
(quarter turns counter-clockwise, e.g. a scan that was fed in sideways), so
the invisible text follows the printed text instead of crossing it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pikepdf

logger = logging.getLogger("paperless.parsing.indra")

_GLYPH_WIDTH = 0.5  # em, must match /DW below
_ASCENT = 0.8  # baseline sits at 80% of the line height

# Text direction and glyph "up" in displayed coordinates (x right, y down)
# for text turned k quarter turns counter-clockwise.
_TEXT_AXES = {
    0: ((1, 0), (0, -1)),
    1: ((0, -1), (-1, 0)),
    2: ((-1, 0), (0, 1)),
    3: ((0, 1), (1, 0)),
}


@dataclass(frozen=True, slots=True)
class TextItem:
    page_idx: int
    bbox: tuple[float, float, float, float]  # fractions of the displayed page
    text: str
    turn: int = 0  # quarter turns counter-clockwise of the text on the displayed page


# Glyphless fonts bundled with OCRmyPDF and the id of a glyph 0.5 em wide:
# Occulta (OCRmyPDF >= 17, ``blank1`` of 1000 units) and Tesseract's
# GlyphLessFont pdf.ttf (OCRmyPDF 16 in Paperless 2.x, ``.null`` of 2048).
_GLYPHLESS_FONTS = (("Occulta.ttf", 4), ("pdf.ttf", 1))


def _font_program() -> tuple[bytes | None, int]:
    """A glyphless font from OCRmyPDF to embed (PDF/A needs embedded fonts)."""
    for name, gid in _GLYPHLESS_FONTS:
        try:
            return resources.files("ocrmypdf.data").joinpath(name).read_bytes(), gid
        except Exception:
            continue
    return None, 1  # pragma: no cover - ocrmypdf is always present in Paperless


def _to_unicode_cmap() -> bytes:
    ranges = [f"<{hi:02X}00> <{hi:02X}FF> <{hi:02X}00>" for hi in range(256) if not 0xD8 <= hi <= 0xDF]
    chunks = []
    for i in range(0, len(ranges), 100):
        part = ranges[i : i + 100]
        chunks.append(f"{len(part)} beginbfrange\n" + "\n".join(part) + "\nendbfrange")
    body = "\n".join(chunks)
    return (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{body}\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ).encode("ascii")


def _make_font(pdf: pikepdf.Pdf) -> pikepdf.Object:
    program, gid = _font_program()
    # Every CID maps to the same blank glyph that is 0.5 em wide.
    cid_to_gid = pdf.make_stream(gid.to_bytes(2, "big") * 65536)
    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor,
        FontName=pikepdf.Name("/IndraGlyphLess"),
        Flags=5,
        FontBBox=[0, -200, 500, 800],
        ItalicAngle=0,
        Ascent=800,
        Descent=-200,
        CapHeight=800,
        StemV=80,
    )
    if program:
        descriptor[pikepdf.Name.FontFile2] = pdf.make_stream(program)
    cid_font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name("/IndraGlyphLess"),
        CIDSystemInfo=pikepdf.Dictionary(
            Registry=pikepdf.String("Adobe"),
            Ordering=pikepdf.String("Identity"),
            Supplement=0,
        ),
        FontDescriptor=pdf.make_indirect(descriptor),
        DW=int(_GLYPH_WIDTH * 1000),
        CIDToGIDMap=cid_to_gid,
    )
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type0,
        BaseFont=pikepdf.Name("/IndraGlyphLess"),
        Encoding=pikepdf.Name("/Identity-H"),
        DescendantFonts=[pdf.make_indirect(cid_font)],
        ToUnicode=pdf.make_stream(_to_unicode_cmap()),
    )
    return pdf.make_indirect(font)


def _encode(text: str) -> str:
    """Identity-H hex string; non-BMP characters become U+FFFD."""
    return "".join(
        f"{ord(c) if ord(c) <= 0xFFFF and not 0xD800 <= ord(c) <= 0xDFFF else 0xFFFD:04X}" for c in text
    )


class _PageGeometry:
    """Maps displayed-page points (top-left origin, y down) to PDF user space."""

    def __init__(self, page: pikepdf.Page) -> None:
        box = page.obj.get("/CropBox") or page.mediabox
        llx, lly, urx, ury = (float(v) for v in box)
        self.llx, self.lly = min(llx, urx), min(lly, ury)
        self.urx, self.ury = max(llx, urx), max(lly, ury)
        self.rotate = int(page.obj.get("/Rotate", 0)) % 360
        w, h = self.urx - self.llx, self.ury - self.lly
        self.disp_w, self.disp_h = (h, w) if self.rotate in (90, 270) else (w, h)

    def vector(self, X: float, Y: float) -> tuple[float, float]:
        """A displayed-space vector (points, y down) in user space."""
        if self.rotate == 90:
            return Y, X
        if self.rotate == 180:
            return -X, Y
        if self.rotate == 270:
            return -Y, -X
        return X, -Y

    def point(self, X: float, Y: float) -> tuple[float, float]:
        """A displayed-space point (points from the top-left corner) in user space."""
        origin = {
            0: (self.llx, self.ury),
            90: (self.llx, self.lly),
            180: (self.urx, self.lly),
            270: (self.urx, self.ury),
        }.get(self.rotate, (self.llx, self.ury))
        vx, vy = self.vector(X, Y)
        return origin[0] + vx, origin[1] + vy


def _line_ops(geo: _PageGeometry, font: str, item: TextItem) -> str:
    """Content stream operators for one text line."""
    text = " ".join(item.text.split())
    fx0, fy0, fx1, fy1 = item.bbox
    x0, x1 = fx0 * geo.disp_w, fx1 * geo.disp_w
    y0, y1 = fy0 * geo.disp_h, fy1 * geo.disp_h
    w, h = x1 - x0, y1 - y0
    if not text or w <= 0 or h <= 0:
        return ""
    (dx, dy), (ux, uy) = _TEXT_AXES[item.turn % 4]
    length = abs(dx) * w + abs(dy) * h
    size = abs(ux) * w + abs(uy) * h  # line thickness = font size
    # Top-left corner of the line as the text sees it: start of the text run
    # (minimum along the direction), top of the glyphs (maximum along "up").
    cx = (x0 if dx > 0 else x1) if dx else (x1 if ux > 0 else x0)
    cy = (y0 if dy > 0 else y1) if dy else (y1 if uy > 0 else y0)
    # Baseline origin sits _ASCENT of the thickness below the top.
    ox, oy = cx - _ASCENT * size * ux, cy - _ASCENT * size * uy
    bx, by = geo.point(ox, oy)
    tdx, tdy = geo.vector(dx, dy)
    tux, tuy = geo.vector(ux, uy)
    natural = len(text) * _GLYPH_WIDTH * size
    hscale = 100.0 * length / natural if natural > 0 else 100.0
    return (
        f"BT 3 Tr /{font} {size:.2f} Tf {hscale:.2f} Tz "
        f"{tdx:g} {tdy:g} {tux:g} {tuy:g} {bx:.2f} {by:.2f} Tm <{_encode(text)}> Tj ET\n"
    )


def page_ops(geo: _PageGeometry, font: str, items: Iterable[TextItem]) -> str:
    return "".join(_line_ops(geo, font, item) for item in items)


def add_text_layer(
    source: Path,
    items: list[TextItem],
    output: Path,
    *,
    skip_pages: set[int] | None = None,
) -> int:
    """Write ``source`` plus invisible text to ``output``; returns pages touched."""
    skip_pages = skip_pages or set()
    by_page: dict[int, list[TextItem]] = {}
    for item in items:
        by_page.setdefault(item.page_idx, []).append(item)

    touched = 0
    with pikepdf.open(source) as pdf:
        font = None
        for page_idx, page in enumerate(pdf.pages):
            page_items = by_page.get(page_idx)
            if not page_items or page_idx in skip_pages:
                continue
            if font is None:
                font = _make_font(pdf)
            name = page.add_resource(font, pikepdf.Name.Font, prefix="FIndra")
            ops = page_ops(_PageGeometry(page), str(name).lstrip("/"), page_items)
            if not ops:
                continue
            # Isolate the original content's graphics state from ours.
            page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
            page.contents_add(pdf.make_stream(b"\nQ\n" + ops.encode("ascii")))
            touched += 1
        pdf.save(output)
    logger.debug("Text layer written to %d page(s) of %s", touched, output)
    return touched
