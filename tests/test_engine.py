"""Box mapping (pure) and the real Indra engine (marked ``models``)."""

from __future__ import annotations

import pytest
from conftest import INVOICE_LINES, render_invoice

from paperless_indra.config import IndraSettings
from paperless_indra.engine import map_box, run

# Where "Musterfirma GmbH, ..." sits on the upright invoice, as fractions of
# the page (the line is drawn at x=150, y=360..410 px on a 1654x2339 px scan).
_UPRIGHT = (150 / 1654, 368 / 2339, 1271 / 1654, 408 / 2339)


def _turned(box, turn):
    """Fractions of a box after turning the page ``turn`` quarter turns CCW."""
    x0, y0, x1, y1 = box
    for _ in range(turn % 4):
        # CCW quarter turn: (x, y) -> (y, 1 - x) in page fractions.
        x0, y0, x1, y1 = y0, 1 - x1, y1, 1 - x0
    return x0, y0, x1, y1


# ----------------------------------------------------------------------
# map_box: Indra reports boxes on rot90(page, -k); undo that.
# ----------------------------------------------------------------------


def test_map_box_upright():
    assert map_box([100, 200, 300, 250], 0, (1000, 2000)) == (0.1, 0.1, 0.3, 0.125)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_map_box_inverts_numpy_rot90(k):
    import numpy as np

    W, H = 40, 60
    page = np.zeros((H, W), np.uint8)
    page[10:14, 5:25] = 1  # a "line" on the displayed page: x 5..25, y 10..14
    turned = np.rot90(page, -k)
    ys, xs = np.nonzero(turned)
    box_turned = [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]
    assert map_box(box_turned, 90 * k, (W, H)) == pytest.approx((5 / W, 10 / H, 25 / W, 14 / H))


def test_map_box_rejects_degenerate_and_clamps():
    assert map_box([10, 10, 10, 20], 0, (100, 100)) is None
    assert map_box([1, 1, 2, 2], 0, (0, 100)) is None
    assert map_box([-5, 90, 50, 120], 0, (100, 100)) == (0.0, 0.9, 0.5, 1.0)


# ----------------------------------------------------------------------
# Real Indra OCR (needs the ~25 MB models; downloaded and cached on first run)
# ----------------------------------------------------------------------


@pytest.mark.models
def test_indra_reads_german_invoice(indra_available, invoice):
    _, pdf = invoice
    pages = run(pdf, IndraSettings.from_env())
    assert len(pages) == 1
    page = pages[0]
    assert page.source == "ocr"
    for line in INVOICE_LINES:
        assert " ".join(line.split()) in " ".join(page.text.split())
    assert "Rechnung Nr. 2026-0815" in page.markdown
    assert len(page.items) == 8
    assert all(item.turn == 0 for item in page.items)
    hit = next(i for i in page.items if i.text.startswith("Musterfirma"))
    assert hit.bbox == pytest.approx(_UPRIGHT, abs=0.01)


@pytest.mark.models
@pytest.mark.parametrize("turn", [1, 2, 3])
def test_indra_turned_scans_map_back_to_the_page(indra_available, tmp_path, turn):
    _, pdf = render_invoice(tmp_path, turn=turn)
    page = run(pdf, IndraSettings.from_env())[0]
    assert "Gesamtbetrag: 1.234,56 EUR" in page.text
    hit = next(i for i in page.items if i.text.startswith("Musterfirma"))
    assert hit.turn == turn
    assert hit.bbox == pytest.approx(_turned(_UPRIGHT, turn), abs=0.01)


@pytest.mark.models
def test_pdf_rotate_attribute_is_respected(indra_available, tmp_path):
    """Upright content stored sideways with /Rotate 90: displayed page is upright."""
    import pikepdf

    _, pdf = render_invoice(tmp_path, turn=1)  # content runs bottom-to-top...
    rotated = tmp_path / "rotate.pdf"
    with pikepdf.open(pdf) as doc:
        doc.pages[0].obj.Rotate = 90  # ...and /Rotate 90 (clockwise) shows it upright
        doc.save(rotated)
    page = run(rotated, IndraSettings.from_env())[0]
    hit = next(i for i in page.items if i.text.startswith("Musterfirma"))
    assert hit.turn == 0
    assert hit.bbox == pytest.approx(_UPRIGHT, abs=0.01)


@pytest.mark.models
def test_text_layer_pages_get_no_items(indra_available, tmp_path):
    """A born-digital page is read from its text layer and needs no overlay."""
    digital = make_digital_pdf(tmp_path / "digital.pdf", ["Rechnung Nr. 2026-0815", "Kundennummer K-4711"])
    page = run(digital, IndraSettings.from_env())[0]
    assert page.source == "text"
    assert "Kundennummer K-4711" in page.text
    assert page.items == []


def make_digital_pdf(path, lines):
    """A visible, born-digital page (Helvetica, ASCII text)."""
    import pikepdf

    doc = pikepdf.new()
    doc.add_blank_page(page_size=(595, 842))
    page = doc.pages[0]
    font = doc.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        ),
    )
    page.add_resource(font, pikepdf.Name.Font, pikepdf.Name.F1)
    ops = "".join(f"BT /F1 18 Tf 72 {760 - 40 * i} Td ({line}) Tj ET\n" for i, line in enumerate(lines))
    page.contents_add(doc.make_stream(ops.encode("latin-1")))
    doc.save(path)
    return path
