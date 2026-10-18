"""Test fixtures.

Unit tests run without Paperless-ngx installed: the few Paperless/Django
modules the parser imports lazily are replaced by small stand-ins that mimic
the v3.3.x signatures (``documents.parsers.ParseError``,
``paperless.parsers.utils.*``, ``paperless.config.OcrConfig``,
``django.conf.settings.SCRATCH_DIR``).
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import img2pdf
import pytest
from PIL import Image, ImageDraw, ImageFont

INVOICE_LINES = [
    "Musterfirma GmbH, Beispielstraße 12, 12345 Musterstadt",
    "Datum: 14.10.2026      Kundennummer: K-4711",
    "Sehr geehrte Damen und Herren,",
    "für die gelieferte Ware erlauben wir uns, folgenden Betrag",
    "in Rechnung zu stellen. Zahlbar innerhalb von 14 Tagen.",
    "Gesamtbetrag: 1.234,56 EUR (inkl. 19 % MwSt.)",
    "Mit freundlichen Grüßen – Ihre Buchhaltung",
]


class FakeParseError(Exception):
    pass


class FakeOcrConfig:
    output_type = "pdf"
    color_conversion_strategy = None
    image_dpi = None


def _post_process_text(text):
    if not text:
        return None
    text = re.sub(r"([^\S\r\n]+)", " ", text)
    text = re.sub(r"([\n\r]+)([^\S\n\r]+)", "\\1", text)
    return text.strip().replace("\0", " ") or None


def _install_paperless_stubs(scratch: Path) -> dict:
    calls: dict = {"born_digital": (None, False), "thumbnail": []}

    django = types.ModuleType("django")
    django_conf = types.ModuleType("django.conf")
    django_conf.settings = types.SimpleNamespace(SCRATCH_DIR=scratch)
    django.conf = django_conf

    documents = types.ModuleType("documents")
    documents_parsers = types.ModuleType("documents.parsers")
    documents_parsers.ParseError = FakeParseError

    def make_thumbnail_from_pdf(in_path, temp_dir, logging_group=None):
        calls["thumbnail"].append(Path(in_path))
        out = Path(temp_dir) / "thumb.webp"
        out.write_bytes(b"webp")
        return out

    documents_parsers.make_thumbnail_from_pdf = make_thumbnail_from_pdf
    documents.parsers = documents_parsers

    paperless = types.ModuleType("paperless")
    paperless_parsers = types.ModuleType("paperless.parsers")
    paperless_utils = types.ModuleType("paperless.parsers.utils")
    paperless_utils.post_process_text = _post_process_text
    paperless_utils.pdf_born_digital_text = lambda path, log=None: calls["born_digital"]

    def get_page_count_for_pdf(path, log=None):
        import pikepdf

        with pikepdf.open(path) as pdf:
            return len(pdf.pages)

    paperless_utils.get_page_count_for_pdf = get_page_count_for_pdf
    paperless_utils.extract_pdf_metadata = lambda path, log=None: []
    paperless_config = types.ModuleType("paperless.config")
    paperless_config.OcrConfig = FakeOcrConfig
    paperless.parsers = paperless_parsers
    paperless_parsers.utils = paperless_utils
    paperless.config = paperless_config

    sys.modules.update(
        {
            "django": django,
            "django.conf": django_conf,
            "documents": documents,
            "documents.parsers": documents_parsers,
            "paperless": paperless,
            "paperless.parsers": paperless_parsers,
            "paperless.parsers.utils": paperless_utils,
            "paperless.config": paperless_config,
        },
    )
    return calls


@pytest.fixture
def paperless_stubs(tmp_path, monkeypatch):
    names = [
        "django",
        "django.conf",
        "documents",
        "documents.parsers",
        "paperless",
        "paperless.parsers",
        "paperless.parsers.utils",
        "paperless.config",
    ]
    saved = {n: sys.modules.get(n) for n in names}
    calls = _install_paperless_stubs(tmp_path / "scratch")
    yield calls
    for n, mod in saved.items():
        if mod is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = mod


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("PAPERLESS_INDRA_") or key in (
            "PAPERLESS_THREADS_PER_WORKER",
            "INDRA_OCR_MODEL_DIR",
        ):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def indra_available() -> bool:
    """True when the real Indra models can be loaded (cached or downloadable)."""
    from paperless_indra.config import IndraSettings
    from paperless_indra.engine import IndraError, get_converter

    try:
        get_converter(IndraSettings.from_env())
    except IndraError as e:
        pytest.skip(f"Indra models unavailable: {e}")
    return True


def render_invoice(directory: Path, *, turn: int = 0) -> tuple[Path, Path]:
    """Synthetic A4 invoice scan at 200 dpi (image-only PDF).

    ``turn`` rotates the *scan content* by 90-degree steps counter-clockwise,
    like a sheet fed into the scanner sideways or upside down.
    """
    w, h = 1654, 2339
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("arial.ttf", 44)
        fb = ImageFont.truetype("arialbd.ttf", 64)
    except OSError:
        f = fb = ImageFont.load_default(size=44)
    d.text((150, 180), "Rechnung Nr. 2026-0815", font=fb, fill=0)
    y = 360
    for line in INVOICE_LINES:
        d.text((150, y), line, font=f, fill=0)
        y += 90
    if turn % 4:
        im = im.rotate(90 * (turn % 4), expand=True)
    png = directory / f"scan{turn or ''}.png"
    im.save(png, dpi=(200, 200))
    pdf = directory / f"scan{turn or ''}.pdf"
    pdf.write_bytes(img2pdf.convert(str(png)))
    return png, pdf


@pytest.fixture
def invoice(tmp_path) -> tuple[Path, Path]:
    return render_invoice(tmp_path)
