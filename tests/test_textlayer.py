import pikepdf
import pytest
from pdfminer.high_level import extract_pages, extract_text
from pdfminer.layout import LTChar

from paperless_indra.textlayer import TextItem, add_text_layer


def _chars(pdf_path):
    def walk(obj):
        if isinstance(obj, LTChar):
            yield obj
        for child in getattr(obj, "_objs", []) or []:
            yield from walk(child)

    return [c for page in extract_pages(str(pdf_path)) for c in walk(page)]


def _blank_pdf(path, rotate=0, size=(595, 842)):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=size)
    if rotate:
        pdf.pages[0].obj.Rotate = rotate
    pdf.save(path)
    return path


def _extent(chars):
    xs = [v for c in chars for v in (c.bbox[0], c.bbox[2])]
    ys = [v for c in chars for v in (c.bbox[1], c.bbox[3])]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize(
    ("rotate", "expected"),
    [
        # bbox x 0.1-0.5, y 0.1-0.15 of the *displayed* page. pdfminer applies
        # /Rotate too, so it reports where a viewer shows the text (y bottom-up).
        (0, (59.5, 715.7, 297.5, 757.8)),
        (90, (84.2, 505.75, 421.0, 535.5)),  # displayed page is 842 x 595
        (180, (59.5, 715.7, 297.5, 757.8)),
        (270, (84.2, 505.75, 421.0, 535.5)),
    ],
)
def test_page_rotation_mapping(tmp_path, rotate, expected):
    src = _blank_pdf(tmp_path / "in.pdf", rotate=rotate)
    out = tmp_path / "out.pdf"
    add_text_layer(src, [TextItem(0, (0.1, 0.1, 0.5, 0.15), "Hallo Welt")], out)
    chars = _chars(out)
    assert "".join(c.get_text() for c in chars) == "Hallo Welt"
    assert _extent(chars) == pytest.approx(expected, abs=2)


@pytest.mark.parametrize("turn", [0, 1, 2, 3])
@pytest.mark.parametrize("rotate", [0, 90])
def test_turned_text_runs_along_the_printed_line(tmp_path, turn, rotate):
    """Text Indra turned upright runs in its printed direction on the page."""
    src = _blank_pdf(tmp_path / "in.pdf", rotate=rotate)
    out = tmp_path / "out.pdf"
    # A tall box for vertical text (turn 1/3), a wide one for horizontal text.
    bbox = (0.4, 0.2, 0.45, 0.7) if turn % 2 else (0.2, 0.4, 0.7, 0.45)
    add_text_layer(src, [TextItem(0, bbox, "ABCDEFGH", turn=turn)], out)
    chars = _chars(out)
    assert sorted(c.get_text() for c in chars) == list("ABCDEFGH")

    geo_w, geo_h = (842, 595) if rotate == 90 else (595, 842)  # displayed page size
    x0, y0, x1, y1 = bbox
    # pdfminer y is bottom-up on the displayed page.
    expected = (x0 * geo_w, (1 - y1) * geo_h, x1 * geo_w, (1 - y0) * geo_h)
    assert _extent(chars) == pytest.approx(expected, abs=2)

    # Reading order: centres advance in the printed direction.
    centres = [((c.bbox[0] + c.bbox[2]) / 2, (c.bbox[1] + c.bbox[3]) / 2) for c in chars]
    by_letter = [centres[[c.get_text() for c in chars].index(ch)] for ch in "ABCDEFGH"]
    (ax, ay), (hx, hy) = by_letter[0], by_letter[-1]
    direction = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[turn]  # y up in pdfminer
    assert (hx - ax) * direction[0] + (hy - ay) * direction[1] > 0


def test_cropbox_offset(tmp_path):
    src = tmp_path / "in.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(700, 900))
    pdf.pages[0].obj.CropBox = [50, 40, 645, 882]
    pdf.save(src)
    out = tmp_path / "out.pdf"
    add_text_layer(src, [TextItem(0, (0.0, 0.0, 1.0, 0.05), "Kopfzeile")], out)
    chars = _chars(out)
    assert min(c.bbox[0] for c in chars) == pytest.approx(50, abs=1)
    assert max(c.bbox[3] for c in chars) == pytest.approx(882, abs=1)


def test_skip_pages_and_non_bmp(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    src = tmp_path / "in.pdf"
    pdf.save(src)
    out = tmp_path / "out.pdf"
    items = [
        TextItem(0, (0.1, 0.1, 0.9, 0.2), "erste"),
        TextItem(1, (0.1, 0.1, 0.9, 0.2), "zweite \U0001f600 Ölmühle"),
    ]
    assert add_text_layer(src, items, out, skip_pages={0}) == 1
    texts = [extract_text(str(out), page_numbers=[i]).strip() for i in range(2)]
    assert texts == ["", "zweite � Ölmühle"]


def test_invisible_and_original_preserved(invoice, tmp_path):
    _, scan_pdf = invoice
    out = tmp_path / "out.pdf"
    add_text_layer(scan_pdf, [TextItem(0, (0.09, 0.157, 0.77, 0.174), "Musterfirma GmbH")], out)
    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        ops = pikepdf.unparse_content_stream(pikepdf.parse_content_stream(page))
        assert ops.startswith(b"q\n")  # original wrapped in q ... Q
        assert b"3 Tr" in ops
        assert len(page.get_images()) == 1
        font = next(iter(page.resources.Font.values()))
        assert font.Subtype == "/Type0" and font.Encoding == "/Identity-H"
        assert "/ToUnicode" in font
