"""Paperless-ngx 3.x parser plugin that runs Datalab's Indra OCR locally.

Like the built-in Tesseract parser, everything happens inside the Paperless
worker process; no extra service is needed. Flow for one document:

1. Born-digital PDF and no archive wanted -> use the PDF's own text.
2. Images are wrapped into a PDF locally (img2pdf), so Indra and the archive
   share one page geometry.
3. Indra reads every page: its own PDF text layer where that is sound, OCR
   elsewhere (``PAPERLESS_INDRA_MODE``). Markdown becomes the content.
4. Only OCR'd lines get an invisible text layer in the archive copy (pages
   that already carry text are left alone), then the archive is converted to
   PDF/A with OCRmyPDF without OCR - Tesseract never runs.

Paperless 2.x uses :mod:`paperless_indra.legacy` instead.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self

from paperless_indra import __version__, core
from paperless_indra.config import IndraSettings
from paperless_indra.engine import IndraError, run

if TYPE_CHECKING:
    import datetime
    from types import TracebackType

    from paperless.parsers import MetadataEntry, ParserContext

logger = logging.getLogger("paperless.parsing.indra")


def _parse_error(message: str) -> Exception:
    from documents.parsers import ParseError

    return ParseError(message)


def _ocr_config():
    try:
        from paperless.config import OcrConfig

        return OcrConfig()
    except Exception:  # pragma: no cover - defensive, config always exists
        return None


class IndraDocumentParser:
    """Local CPU OCR with Indra (ONNX Runtime), searchable archive PDF."""

    name: str = "Paperless-ngx Indra OCR Parser"
    version: str = __version__
    author: str = "paperless-indra contributors"
    url: str = "https://github.com/Pacman1984/paperless-ngx-indra-plugin"

    # Runs fully inside the Paperless container.
    uses_remote_service: bool = False

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def supported_mime_types(cls) -> dict[str, str]:
        return core.SUPPORTED_MIME_TYPES

    @classmethod
    def score(cls, mime_type: str, filename: str, path: Path | None = None) -> int | None:
        if mime_type not in core.SUPPORTED_MIME_TYPES:
            return None
        settings = IndraSettings.from_env()
        if not settings.enabled:
            return None
        # 15 beats Tesseract (10) and stays below remote engines (20), so a
        # remote parser can still take documents a workflow marks for it.
        return settings.score

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def can_produce_archive(self) -> bool:
        return True

    @property
    def requires_pdf_rendition(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, logging_group: object = None) -> None:
        from django.conf import settings as django_settings

        django_settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._tempdir = Path(tempfile.mkdtemp(prefix="paperless-indra-", dir=django_settings.SCRATCH_DIR))
        self._logging_group = logging_group
        self._settings = IndraSettings.from_env()
        self._text: str | None = None
        self._archive_path: Path | None = None
        self._image_pdf: Path | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.debug("Cleaning up temporary directory %s", self._tempdir)
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def configure(self, context: ParserContext) -> None:
        pass

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self, document_path: Path, mime_type: str, *, produce_archive: bool = True) -> None:
        document_path = Path(document_path)

        if mime_type == "application/pdf" and not produce_archive:
            from paperless.parsers.utils import pdf_born_digital_text

            text, born_digital = pdf_born_digital_text(document_path, log=logger)
            if born_digital:
                logger.debug("Indra: skipped - born-digital PDF and no archive requested")
                self._text = text or ""
                return

        source_pdf = document_path if mime_type == "application/pdf" else self._image_to_pdf(document_path)

        try:
            pages = run(source_pdf, self._settings)
        except IndraError as e:
            logger.error("Indra OCR failed: %s", e)
            raise _parse_error(f"Indra OCR failed: {e}") from e

        core.log_summary(document_path.name, pages)
        if failed := core.all_pages_failed(pages):
            raise _parse_error(f"Indra OCR failed on every page: {failed}")

        self._text = core.content_from_pages(pages, self._settings)

        if not produce_archive or not self._settings.text_layer:
            return
        items = [item for p in pages for item in p.items]
        if not items:
            logger.debug("Indra: no OCR'd lines, no archive needed")
            return
        ocr_config = _ocr_config()
        try:
            self._archive_path = core.build_archive(
                source_pdf,
                items,
                self._tempdir,
                is_pdf_input=mime_type == "application/pdf",
                output_type=str(ocr_config.output_type) if ocr_config else None,
                color_conversion_strategy=getattr(ocr_config, "color_conversion_strategy", None),
                settings=self._settings,
            )
        except core.ArchiveError as e:
            raise _parse_error(str(e)) from e

    def _image_to_pdf(self, image: Path) -> Path:
        if self._image_pdf is None:
            ocr_config = _ocr_config()
            try:
                self._image_pdf = core.image_to_pdf(
                    image,
                    self._tempdir,
                    getattr(ocr_config, "image_dpi", None),
                )
            except core.ArchiveError as e:
                raise _parse_error(str(e)) from e
        return self._image_pdf

    # ------------------------------------------------------------------
    # Result accessors
    # ------------------------------------------------------------------

    def get_text(self) -> str:
        return self._text or ""

    def get_date(self) -> datetime.datetime | None:
        return None

    def get_archive_path(self) -> Path | None:
        return self._archive_path

    # ------------------------------------------------------------------
    # Thumbnail, page count, metadata
    # ------------------------------------------------------------------

    def get_thumbnail(self, document_path: Path, mime_type: str) -> Path:
        from documents.parsers import make_thumbnail_from_pdf

        if self._archive_path is not None:
            pdf = self._archive_path
        elif mime_type == "application/pdf":
            pdf = Path(document_path)
        else:
            pdf = self._image_to_pdf(Path(document_path))
        return make_thumbnail_from_pdf(pdf, self._tempdir, self._logging_group)

    def get_page_count(self, document_path: Path, mime_type: str) -> int | None:
        from paperless.parsers.utils import get_page_count_for_pdf

        if mime_type == "application/pdf":
            return get_page_count_for_pdf(Path(document_path), log=logger)
        if self._archive_path is not None:
            return get_page_count_for_pdf(self._archive_path, log=logger)
        return None

    def extract_metadata(self, document_path: Path, mime_type: str) -> list[MetadataEntry]:
        if mime_type != "application/pdf":
            return []
        from paperless.parsers.utils import extract_pdf_metadata

        return extract_pdf_metadata(Path(document_path), log=logger)
