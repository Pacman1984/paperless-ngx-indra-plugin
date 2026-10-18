"""Environment-driven configuration for the Indra parser.

Everything is read from ``PAPERLESS_INDRA_*`` environment variables so the
plugin can be configured in ``docker-compose.env`` next to the regular
Paperless settings. No Django imports, so it can be unit-tested in isolation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("paperless.parsing.indra")

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    logger.warning("Invalid boolean %s=%r, using default %s.", name, raw, default)
    return default


def _env_number(name: str, default: float, cast=float, minimum: float | None = None):
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        logger.warning("Invalid number %s=%r, using default %s.", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("%s=%r is below %s, using default %s.", name, raw, minimum, default)
        return default
    return value


def _env_choice(name: str, choices: tuple[str, ...], default: str) -> str:
    raw = _env_str(name)
    if raw is None:
        return default
    if raw not in choices:
        logger.warning(
            "Invalid value %s=%r (valid: %s), using default %r.",
            name,
            raw,
            ", ".join(choices),
            default,
        )
        return default
    return raw


@dataclass(frozen=True, slots=True)
class IndraSettings:
    enabled: bool
    score: int
    threads: int
    dpi: float
    mode: str
    content_format: str
    text_layer: bool
    pdfa: bool
    model_dir: str | None
    keep_furniture: bool
    math: bool

    @classmethod
    def from_env(cls) -> IndraSettings:
        default_threads = _env_number("PAPERLESS_THREADS_PER_WORKER", 2, int, minimum=1)
        return cls(
            enabled=_env_bool("PAPERLESS_INDRA_ENABLED", True),
            score=_env_number("PAPERLESS_INDRA_SCORE", 15, int),
            threads=_env_number("PAPERLESS_INDRA_THREADS", default_threads, int, minimum=1),
            dpi=_env_number("PAPERLESS_INDRA_DPI", 200.0, float, minimum=72),
            mode=_env_choice("PAPERLESS_INDRA_MODE", ("auto", "ocr", "text"), "auto"),
            content_format=_env_choice("PAPERLESS_INDRA_CONTENT_FORMAT", ("markdown", "text"), "markdown"),
            text_layer=_env_bool("PAPERLESS_INDRA_TEXT_LAYER", True),
            pdfa=_env_bool("PAPERLESS_INDRA_PDFA", True),
            model_dir=_env_str("PAPERLESS_INDRA_MODEL_DIR") or _env_str("INDRA_OCR_MODEL_DIR"),
            keep_furniture=_env_bool("PAPERLESS_INDRA_KEEP_FURNITURE", False),
            math=_env_bool("PAPERLESS_INDRA_MATH", False),
        )

    def converter_key(self) -> tuple:
        """Settings that require a new Indra converter when they change."""
        return (self.threads, self.dpi, self.mode, self.model_dir, self.keep_furniture, self.math)
