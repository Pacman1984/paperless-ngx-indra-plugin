"""Indra parser for Paperless-ngx 2.x.

Subclasses Paperless' own Tesseract parser, so thumbnails, page counts and
metadata behave exactly as before; only ``parse()`` is replaced and never
starts Tesseract. Archive decisions follow the 2.x settings
``PAPERLESS_OCR_SKIP_ARCHIVE_FILE`` and ``PAPERLESS_OCR_MODE=skip_noarchive``.
"""

from __future__ import annotations

from pathlib import Path

from documents.parsers import ParseError
from paperless.models import ArchiveFileChoices, ModeChoices
from paperless_tesseract.parsers import RasterisedDocumentParser

from paperless_indra import core
from paperless_indra.config import IndraSettings
from paperless_indra.engine import IndraError, run


class IndraLegacyDocumentParser(RasterisedDocumentParser):
    """Local CPU OCR with Indra (ONNX Runtime) for Paperless 2.x."""

    logging_name = "paperless.parsing.indra"

    def parse(self, document_path, mime_type, file_name=None):
        indra = IndraSettings.from_env()
        document_path = Path(document_path)
        is_pdf = mime_type == "application/pdf"

        original_text = core.pdf_text(document_path) if is_pdf else ""
        original_has_text = len(original_text) > core.PDF_TEXT_MIN_LENGTH
        skip_archive_for_text = self.settings.mode == ModeChoices.SKIP_NO_ARCHIVE or (
            self.settings.skip_archive_file in {ArchiveFileChoices.WITH_TEXT, ArchiveFileChoices.ALWAYS}
        )
        if skip_archive_for_text and original_has_text:
            self.log.debug("Document has text and no archive is wanted, skipping Indra.")
            self.text = original_text
            return

        try:
            source_pdf = (
                document_path
                if is_pdf
                else core.image_to_pdf(document_path, self.tempdir, self.settings.image_dpi)
            )
        except core.ArchiveError as e:
            raise ParseError(str(e)) from e

        try:
            pages = run(source_pdf, indra)
        except IndraError as e:
            self.log.error(f"Indra OCR failed: {e}")
            raise ParseError(f"Indra OCR failed: {e}") from e

        core.log_summary(Path(file_name or document_path).name, pages)
        if failed := core.all_pages_failed(pages):
            raise ParseError(f"Indra OCR failed on every page: {failed}")

        self.text = core.content_from_pages(pages, indra)
        if not self.text and original_has_text:
            self.text = original_text

        if self.settings.skip_archive_file == ArchiveFileChoices.ALWAYS or not indra.text_layer:
            return
        items = [item for p in pages for item in p.items]
        if not items:
            self.log.debug("Indra: no OCR'd lines, no archive needed")
            return
        try:
            self.archive_path = core.build_archive(
                source_pdf,
                items,
                Path(self.tempdir),
                is_pdf_input=is_pdf,
                output_type=str(self.settings.output_type),
                color_conversion_strategy=self.settings.color_conversion_strategy,
                settings=indra,
            )
        except core.ArchiveError as e:
            raise ParseError(str(e)) from e
