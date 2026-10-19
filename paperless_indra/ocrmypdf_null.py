"""OCRmyPDF plugin: a no-op OCR engine for OCRmyPDF 16.x (Paperless 2.x).

OCRmyPDF 17 ships this as ``--ocr-engine none``. Paperless 2.x bundles
OCRmyPDF 16, so the plugin passes this module via ``plugins=[...]`` when it
only wants PDF/A conversion: pages are never handed to Tesseract. Modelled
on OCRmyPDF 17's built-in ``null_ocr`` plugin.
"""

from __future__ import annotations

from pathlib import Path

from ocrmypdf import hookimpl
from ocrmypdf.pluginspec import OcrEngine, OrientationConfidence
from PIL import Image


class NullOcrEngine(OcrEngine):
    @staticmethod
    def version() -> str:
        return "none"

    @staticmethod
    def creator_tag(options) -> str:
        return "OCRmyPDF (no OCR, paperless-indra)"

    def __str__(self) -> str:
        return "No OCR engine (paperless-indra)"

    @staticmethod
    def languages(options) -> set[str]:
        # Accept whatever language Paperless passes; nothing is recognized.
        return set(getattr(options, "languages", None) or ["eng"])

    @staticmethod
    def get_orientation(input_file: Path, options) -> OrientationConfidence:
        return OrientationConfidence(angle=0, confidence=0.0)

    @staticmethod
    def get_deskew(input_file: Path, options) -> float:
        return 0.0

    @staticmethod
    def generate_hocr(input_file: Path, output_hocr: Path, output_text: Path, options) -> None:
        with Image.open(input_file) as img:
            width, height = img.size
        Path(output_hocr).write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"'
            ' "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">\n'
            "<head><title>no OCR</title>"
            '<meta http-equiv="Content-Type" content="text/html;charset=utf-8"/>'
            "<meta name='ocr-system' content='paperless-indra null engine'/></head>\n"
            f"<body><div class='ocr_page' title='bbox 0 0 {width} {height}'></div></body>\n"
            "</html>\n",
            encoding="utf-8",
        )
        Path(output_text).write_text("", encoding="utf-8")

    @staticmethod
    def generate_pdf(input_file: Path, output_pdf: Path, output_text: Path, options) -> None:
        raise NotImplementedError("use pdf_renderer='hocr' with the null OCR engine")


@hookimpl
def get_ocr_engine():
    return NullOcrEngine()
