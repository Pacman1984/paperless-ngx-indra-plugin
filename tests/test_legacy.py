"""Paperless 2.x compatibility (PAPERLESS_APPS + document_consumer_declaration).

Paperless 2.x is stubbed with the pieces the legacy app touches: Django's
AppConfig, the consumer declaration signal, ParseError, the 2.x Tesseract
parser base class and the archive/mode choices.
"""

from __future__ import annotations

import enum
import logging
import sys
import types
from pathlib import Path

import pytest
from conftest import INVOICE_LINES
from pdfminer.high_level import extract_text

from paperless_indra.engine import IndraError, PageResult
from paperless_indra.textlayer import TextItem

_LINES = ["Rechnung Nr. 2026-0815", *INVOICE_LINES]

_MODULES = [
    "django",
    "django.apps",
    "documents",
    "documents.parsers",
    "documents.signals",
    "paperless",
    "paperless.models",
    "paperless.parsers",
    "paperless.parsers.registry",
    "paperless_tesseract",
    "paperless_tesseract.parsers",
    "paperless_indra.legacy.apps",
    "paperless_indra.legacy.parser",
]


class ParseError(Exception):
    pass


class Signal:
    def __init__(self):
        self.receivers = {}

    def connect(self, receiver, dispatch_uid=None):
        self.receivers[dispatch_uid or id(receiver)] = receiver

    def send(self, sender):
        return [(r, r(sender)) for r in self.receivers.values()]


class ModeChoices(enum.StrEnum):
    SKIP = "skip"
    REDO = "redo"
    FORCE = "force"
    SKIP_NO_ARCHIVE = "skip_noarchive"


class ArchiveFileChoices(enum.StrEnum):
    NEVER = "never"
    WITH_TEXT = "with_text"
    ALWAYS = "always"


class OcrSettings:
    mode = ModeChoices.SKIP
    skip_archive_file = ArchiveFileChoices.NEVER
    output_type = "pdf"
    image_dpi = None
    color_conversion_strategy = None


class RasterisedDocumentParser:
    """Shape of paperless_tesseract.parsers.RasterisedDocumentParser (2.20)."""

    logging_name = "paperless.parsing.tesseract"

    def __init__(self, logging_group, progress_callback=None):
        self.logging_group = logging_group
        self.settings = OcrSettings()
        self.tempdir = Path(RasterisedDocumentParser.scratch) / "parser"
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.archive_path = None
        self.text = None
        self.date = None
        self.log = logging.getLogger(self.logging_name)

    def parse(self, document_path, mime_type, file_name=None):  # pragma: no cover
        raise AssertionError("Tesseract must not run")

    def get_text(self):
        return self.text

    def get_archive_path(self):
        return self.archive_path


@pytest.fixture
def paperless2(tmp_path, monkeypatch):
    import paperless_indra.legacy as legacy_pkg

    saved = {n: sys.modules.get(n) for n in _MODULES}
    for name in _MODULES:
        sys.modules.pop(name, None)
    # Fresh legacy modules per test (they bind the stubbed Paperless modules).
    for attr in ("apps", "parser"):
        legacy_pkg.__dict__.pop(attr, None)

    class AppConfig:
        def __init__(self, app_name=None, app_module=None):
            pass

    django = types.ModuleType("django")
    django_apps = types.ModuleType("django.apps")
    django_apps.AppConfig = AppConfig
    documents = types.ModuleType("documents")
    documents_parsers = types.ModuleType("documents.parsers")
    documents_parsers.ParseError = ParseError
    documents_signals = types.ModuleType("documents.signals")
    documents_signals.document_consumer_declaration = Signal()
    paperless = types.ModuleType("paperless")
    paperless.__path__ = []  # a package without parsers.registry (2.x)
    paperless_models = types.ModuleType("paperless.models")
    paperless_models.ModeChoices = ModeChoices
    paperless_models.ArchiveFileChoices = ArchiveFileChoices
    tesseract = types.ModuleType("paperless_tesseract")
    tesseract_parsers = types.ModuleType("paperless_tesseract.parsers")
    RasterisedDocumentParser.scratch = tmp_path / "scratch"
    tesseract_parsers.RasterisedDocumentParser = RasterisedDocumentParser
    sys.modules.update(
        {
            "django": django,
            "django.apps": django_apps,
            "documents": documents,
            "documents.parsers": documents_parsers,
            "documents.signals": documents_signals,
            "paperless": paperless,
            "paperless.models": paperless_models,
            "paperless_tesseract": tesseract,
            "paperless_tesseract.parsers": tesseract_parsers,
        },
    )
    OcrSettings.mode = ModeChoices.SKIP
    OcrSettings.skip_archive_file = ArchiveFileChoices.NEVER
    OcrSettings.output_type = "pdf"
    yield documents_signals.document_consumer_declaration
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _ready():
    from paperless_indra.legacy.apps import PaperlessIndraLegacyConfig

    PaperlessIndraLegacyConfig("paperless_indra.legacy", None).ready()


def _invoice_pages(source="ocr"):
    items = [TextItem(0, (0.09, 0.08 + 0.04 * i, 0.77, 0.1 + 0.04 * i), t) for i, t in enumerate(_LINES)]
    return [
        PageResult(
            0,
            source,
            "## " + _LINES[0] + "\n\n" + "\n\n".join(_LINES[1:]),
            "\n".join(_LINES),
            items if source == "ocr" else [],
        ),
    ]


@pytest.fixture
def fake_run(monkeypatch):
    calls = []
    result = {"pages": _invoice_pages()}

    def run(pdf, settings):
        calls.append(pdf)
        if isinstance(result["pages"], Exception):
            raise result["pages"]
        return result["pages"]

    def install():
        import paperless_indra.legacy.parser as legacy_parser

        monkeypatch.setattr(legacy_parser, "run", run)
        return legacy_parser

    return types.SimpleNamespace(calls=calls, result=result, install=install)


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------


def test_registers_through_signal_and_beats_tesseract(paperless2):
    _ready()
    declarations = [d for _, d in paperless2.send(None)]
    assert len(declarations) == 1
    decl = declarations[0]
    assert decl["weight"] == 15 > 0  # Tesseract declares weight 0
    assert decl["mime_types"]["application/pdf"] == ".pdf"
    assert decl["mime_types"]["image/png"] == ".png"
    parser = decl["parser"]("group", progress_callback=None)
    assert type(parser).__name__ == "IndraLegacyDocumentParser"
    assert isinstance(parser, RasterisedDocumentParser)
    assert parser.logging_name == "paperless.parsing.indra"


def test_ready_is_idempotent(paperless2):
    _ready()
    _ready()
    assert len(paperless2.send(None)) == 1


def test_disabled_declares_no_mime_types(paperless2, monkeypatch):
    monkeypatch.setenv("PAPERLESS_INDRA_ENABLED", "false")
    _ready()
    assert paperless2.send(None)[0][1]["mime_types"] == {}


def test_inactive_on_paperless_3(paperless2, monkeypatch):
    from paperless_indra.legacy import apps

    monkeypatch.setattr(apps, "paperless_has_parser_registry", lambda: True)
    _ready()
    assert paperless2.send(None) == []


def test_registry_detection_on_2x_stub(paperless2):
    from paperless_indra.legacy.apps import paperless_has_parser_registry

    assert paperless_has_parser_registry() is False


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def _parser():
    from paperless_indra.legacy.apps import get_parser

    return get_parser("group")


def test_scanned_pdf_gets_text_and_archive(paperless2, fake_run, invoice):
    fake_run.install()
    _, scan_pdf = invoice
    p = _parser()
    p.parse(scan_pdf, "application/pdf", "rechnung.pdf")
    assert p.get_text().startswith("## Rechnung Nr. 2026-0815")
    archive = p.get_archive_path()
    assert archive is not None
    assert "Gesamtbetrag: 1.234,56 EUR" in extract_text(str(archive))
    assert fake_run.calls == [scan_pdf]


def test_image_is_wrapped_into_pdf(paperless2, fake_run, invoice):
    fake_run.install()
    png, _ = invoice
    p = _parser()
    p.parse(png, "image/png", "scan.png")
    assert fake_run.calls[0].suffix == ".pdf"
    assert "Musterfirma GmbH" in extract_text(str(p.get_archive_path()))


@pytest.mark.parametrize(
    ("mode", "skip_archive", "has_text", "runs_indra", "archive"),
    [
        (ModeChoices.SKIP, ArchiveFileChoices.NEVER, False, True, True),
        (ModeChoices.SKIP, ArchiveFileChoices.WITH_TEXT, False, True, True),
        (ModeChoices.SKIP, ArchiveFileChoices.WITH_TEXT, True, False, False),
        (ModeChoices.SKIP, ArchiveFileChoices.ALWAYS, True, False, False),
        (ModeChoices.SKIP, ArchiveFileChoices.ALWAYS, False, True, False),
        (ModeChoices.SKIP_NO_ARCHIVE, ArchiveFileChoices.NEVER, True, False, False),
    ],
)
def test_archive_settings_follow_paperless_2x(
    paperless2,
    fake_run,
    invoice,
    monkeypatch,
    mode,
    skip_archive,
    has_text,
    runs_indra,
    archive,
):
    from paperless_indra import core

    legacy_parser = fake_run.install()
    OcrSettings.mode = mode
    OcrSettings.skip_archive_file = skip_archive
    existing = "Bereits vorhandener Text einer digitalen Rechnung mit mehr als fuenfzig Zeichen."
    monkeypatch.setattr(core, "pdf_text", lambda pdf: existing if has_text else "")
    assert legacy_parser.core is core
    _, scan_pdf = invoice
    p = _parser()
    p.parse(scan_pdf, "application/pdf", "doc.pdf")
    assert bool(fake_run.calls) is runs_indra
    assert (p.get_archive_path() is not None) is archive
    assert p.get_text() == (existing if not runs_indra else p.get_text())


def test_failure_raises_parse_error(paperless2, fake_run, invoice):
    fake_run.install()
    fake_run.result["pages"] = IndraError("models missing")
    _, scan_pdf = invoice
    with pytest.raises(ParseError, match="models missing"):
        _parser().parse(scan_pdf, "application/pdf")


def test_pdfa_on_ocrmypdf_16_uses_null_engine_plugin(paperless2, fake_run, invoice, monkeypatch):
    import ocrmypdf

    from paperless_indra import core

    fake_run.install()
    calls = []

    def fake_ocr(src, dst, **kwargs):
        calls.append(kwargs)
        dst.write_bytes(src.read_bytes())

    monkeypatch.setattr(ocrmypdf, "ocr", fake_ocr)
    monkeypatch.setattr(core, "_ocrmypdf_major", lambda: 16)
    OcrSettings.output_type = "pdfa"
    _, scan_pdf = invoice
    p = _parser()
    p.parse(scan_pdf, "application/pdf")
    assert p.get_archive_path().name == "archive.pdf"
    assert calls == [
        {
            "output_type": "pdfa",
            "skip_text": True,
            "progress_bar": False,
            "plugins": ["paperless_indra.ocrmypdf_null"],
            "pdf_renderer": "hocr",
        },
    ]


# ----------------------------------------------------------------------
# OCRmyPDF null engine plugin
# ----------------------------------------------------------------------


def test_null_engine_writes_empty_hocr(tmp_path):
    from PIL import Image

    from paperless_indra.ocrmypdf_null import NullOcrEngine, get_ocr_engine

    engine = get_ocr_engine()
    assert isinstance(engine, NullOcrEngine)
    img = tmp_path / "page.png"
    Image.new("L", (120, 80), 255).save(img)
    hocr, txt = tmp_path / "p.hocr", tmp_path / "p.txt"
    engine.generate_hocr(img, hocr, txt, types.SimpleNamespace(languages=["deu"]))
    assert "bbox 0 0 120 80" in hocr.read_text(encoding="utf-8")
    assert txt.read_text(encoding="utf-8") == ""
    assert engine.languages(types.SimpleNamespace(languages=["deu"])) == {"deu"}
    assert engine.get_deskew(img, None) == 0.0
    with pytest.raises(NotImplementedError):
        engine.generate_pdf(img, tmp_path / "o.pdf", txt, None)
