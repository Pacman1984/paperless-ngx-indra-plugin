from __future__ import annotations

import importlib.util
import logging

from django.apps import AppConfig

from paperless_indra import __version__
from paperless_indra.config import IndraSettings
from paperless_indra.core import SUPPORTED_MIME_TYPES

logger = logging.getLogger("paperless.parsing.indra")

PARSER_NAME = "Paperless-ngx Indra OCR Parser"


def get_parser(*args, **kwargs):
    from paperless_indra.legacy.parser import IndraLegacyDocumentParser

    return IndraLegacyDocumentParser(*args, **kwargs)


def indra_consumer_declaration(sender, **kwargs):
    """Paperless 2.x parser declaration; the highest weight wins (Tesseract: 0)."""
    settings = IndraSettings.from_env()
    return {
        "parser": get_parser,
        "weight": settings.score,
        "mime_types": SUPPORTED_MIME_TYPES if settings.enabled else {},
    }


def paperless_has_parser_registry() -> bool:
    """Paperless >= 3.0 discovers plugins through its parser registry."""
    try:
        return importlib.util.find_spec("paperless.parsers.registry") is not None
    except (ImportError, ValueError):
        return False


class PaperlessIndraLegacyConfig(AppConfig):
    name = "paperless_indra.legacy"
    label = "paperless_indra_legacy"
    verbose_name = "Indra OCR parser (Paperless 2.x compatibility)"

    def ready(self) -> None:
        if paperless_has_parser_registry():
            logger.info(
                "paperless_indra.legacy is not needed on Paperless >= 3.0 (the parser is "
                "loaded through its entry point); remove it from PAPERLESS_APPS.",
            )
            return
        from documents.signals import document_consumer_declaration

        document_consumer_declaration.connect(
            indra_consumer_declaration,
            dispatch_uid="paperless_indra_legacy",
        )
        settings = IndraSettings.from_env()
        logger.info(
            "Registered parser '%s' v%s for Paperless 2.x (weight %d%s).",
            PARSER_NAME,
            __version__,
            settings.score,
            "" if settings.enabled else ", disabled by PAPERLESS_INDRA_ENABLED",
        )
