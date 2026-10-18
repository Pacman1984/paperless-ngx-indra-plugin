"""Parser behaviour with Paperless modules stubbed and the Indra engine faked."""

from __future__ import annotations

import pikepdf
import pytest
from conftest import INVOICE_LINES, FakeOcrConfig, FakeParseError
from pdfminer.high_level import extract_text

from paperless_indra import parser as parser_mod
from paperless_indra.engine import IndraError, PageResult
from paperless_indra.parser import IndraDocumentParser
from paperless_indra.textlayer import TextItem

_REQUIRED_ATTRS = ("name", "version", "author", "url", "supported_mime_types", "score")

_LINES = ["Rechnung Nr. 2026-0815", *INVOICE_LINES]


def _invoice_page(idx=0, source="ocr") -> PageResult:
    items = [
        TextItem(idx, (0.09, 0.08 + 0.04 * i, 0.77, 0.1 + 0.04 * i), text) for i, text in enumerate(_LINES)
    ]
    return PageResult(
        page_idx=idx,
        source=source,
        markdown="## " + _LINES[0] + "\n\n" + "\n\n".join(_LINES[1:]),
        text="\n".join(_LINES),
        items=items if source == "ocr" else [],
    )


class FakeEngine:
    pages: list[PageResult] | Exception = []
    calls: list = []

    @classmethod
    def run(cls, pdf, settings):
        cls.calls.append((pdf, settings))
        if isinstance(cls.pages, Exception):
            raise cls.pages
        return cls.pages


@pytest.fixture
def fake_engine(monkeypatch):
    FakeEngine.pages = [_invoice_page()]
    FakeEngine.calls = []
    monkeypatch.setattr(parser_mod, "run", FakeEngine.run)
    return FakeEngine


def test_registry_contract():
    for attr in _REQUIRED_ATTRS:
        assert hasattr(IndraDocumentParser, attr)
    assert IndraDocumentParser.uses_remote_service is False
    assert IndraDocumentParser.supported_mime_types()["image/png"] == ".png"
    for method in (
        "configure",
        "parse",
        "get_text",
        "get_date",
        "get_archive_path",
        "get_thumbnail",
        "get_page_count",
        "extract_metadata",
        "__enter__",
        "__exit__",
    ):
        assert callable(getattr(IndraDocumentParser, method))


def test_score(monkeypatch):
    assert IndraDocumentParser.score("application/pdf", "a.pdf") == 15
    assert IndraDocumentParser.score("image/jpeg", "a.jpg") == 15
    assert IndraDocumentParser.score("text/plain", "a.txt") is None
    monkeypatch.setenv("PAPERLESS_INDRA_SCORE", "30")
    assert IndraDocumentParser.score("application/pdf", "a.pdf") == 30
    monkeypatch.setenv("PAPERLESS_INDRA_ENABLED", "false")
    assert IndraDocumentParser.score("application/pdf", "a.pdf") is None


def test_scanned_pdf_gets_searchable_archive(paperless_stubs, fake_engine, invoice):
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        assert p.can_produce_archive and not p.requires_pdf_rendition
        p.parse(scan_pdf, "application/pdf", produce_archive=True)
        assert p.get_text().startswith("## Rechnung Nr. 2026-0815")
        archive = p.get_archive_path()
        assert archive is not None
        assert "Gesamtbetrag: 1.234,56 EUR" in extract_text(str(archive))
        assert p.get_page_count(scan_pdf, "application/pdf") == 1
        assert p.get_thumbnail(scan_pdf, "application/pdf").exists()
        assert paperless_stubs["thumbnail"][-1] == archive
        assert p.get_date() is None
        tempdir = p._tempdir
    assert not tempdir.exists()
    assert fake_engine.calls[0][0] == scan_pdf


def test_content_format_text(paperless_stubs, fake_engine, invoice, monkeypatch):
    monkeypatch.setenv("PAPERLESS_INDRA_CONTENT_FORMAT", "text")
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert p.get_text().startswith("Rechnung Nr. 2026-0815\nMusterfirma")
        assert "##" not in p.get_text()


def test_multi_page_content_is_joined(paperless_stubs, fake_engine, invoice):
    fake_engine.pages = [_invoice_page(0), PageResult(1, "ocr", "Seite zwei", "Seite zwei")]
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert p.get_text().endswith("Ihre Buchhaltung\n\nSeite zwei")


def test_born_digital_without_archive_skips_indra(paperless_stubs, fake_engine, invoice):
    paperless_stubs["born_digital"] = ("Vorhandener Text", True)
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf", produce_archive=False)
        assert p.get_text() == "Vorhandener Text"
        assert p.get_archive_path() is None
    assert fake_engine.calls == []


def test_text_layer_pages_need_no_archive(paperless_stubs, fake_engine, invoice):
    fake_engine.pages = [_invoice_page(source="text")]
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert "Musterfirma" in p.get_text()
        assert p.get_archive_path() is None


def test_image_is_wrapped_into_pdf(paperless_stubs, fake_engine, invoice):
    png, _ = invoice
    with IndraDocumentParser() as p:
        p.parse(png, "image/png")
        uploaded = fake_engine.calls[0][0]
        assert uploaded.suffix == ".pdf"
        with pikepdf.open(uploaded) as pdf:
            assert float(pdf.pages[0].mediabox[2]) == pytest.approx(595.44, abs=0.5)
        assert "Musterfirma GmbH" in extract_text(str(p.get_archive_path()))
        assert p.get_page_count(png, "image/png") == 1


def test_pages_with_text_are_not_overlaid(paperless_stubs, fake_engine, invoice, monkeypatch):
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        monkeypatch.setattr(p, "_pages_with_text", lambda pdf: {0})
        p.parse(scan_pdf, "application/pdf")
        assert extract_text(str(p.get_archive_path())).strip() == ""


def test_text_layer_disabled(paperless_stubs, fake_engine, invoice, monkeypatch):
    monkeypatch.setenv("PAPERLESS_INDRA_TEXT_LAYER", "false")
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert p.get_archive_path() is None
        assert p.get_text()


def test_engine_failure_raises_parse_error(paperless_stubs, fake_engine, invoice):
    fake_engine.pages = IndraError("models missing")
    _, scan_pdf = invoice
    with IndraDocumentParser() as p, pytest.raises(FakeParseError, match="models missing"):
        p.parse(scan_pdf, "application/pdf")


def test_all_pages_failed_raises(paperless_stubs, fake_engine, invoice):
    fake_engine.pages = [PageResult(0, "error", "", "", note="ValueError: broken")]
    _, scan_pdf = invoice
    with IndraDocumentParser() as p, pytest.raises(FakeParseError, match="broken"):
        p.parse(scan_pdf, "application/pdf")


def test_pdfa_conversion_uses_ocrmypdf_without_ocr(paperless_stubs, fake_engine, invoice, monkeypatch):
    calls = []

    def fake_ocr(src, dst, **kwargs):
        calls.append(kwargs)
        dst.write_bytes(src.read_bytes())

    import ocrmypdf

    monkeypatch.setattr(ocrmypdf, "ocr", fake_ocr)
    monkeypatch.setattr(FakeOcrConfig, "output_type", "pdfa-2")
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert p.get_archive_path().name == "archive.pdf"
    assert calls == [
        {"output_type": "pdfa-2", "skip_text": True, "ocr_engine": "none", "progress_bar": False},
    ]


@pytest.mark.models
def test_end_to_end_with_real_indra(paperless_stubs, indra_available, invoice):
    """The real engine through the parser: content and archive text layer."""
    _, scan_pdf = invoice
    with IndraDocumentParser() as p:
        p.parse(scan_pdf, "application/pdf")
        assert "Gesamtbetrag: 1.234,56 EUR" in p.get_text()
        layer = " ".join(extract_text(str(p.get_archive_path())).split())
        for line in _LINES:
            assert " ".join(line.split()) in layer
