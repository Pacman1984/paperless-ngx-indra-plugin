from paperless_indra.config import IndraSettings


def test_defaults():
    s = IndraSettings.from_env()
    assert s.enabled
    assert s.score == 15
    assert s.threads == 2
    assert s.dpi == 200.0
    assert s.mode == "auto"
    assert s.content_format == "markdown"
    assert s.text_layer and s.pdfa
    assert s.model_dir is None
    assert not s.keep_furniture and not s.math


def test_reads_env(monkeypatch):
    monkeypatch.setenv("PAPERLESS_INDRA_ENABLED", "false")
    monkeypatch.setenv("PAPERLESS_INDRA_SCORE", "25")
    monkeypatch.setenv("PAPERLESS_INDRA_THREADS", "6")
    monkeypatch.setenv("PAPERLESS_INDRA_DPI", "300")
    monkeypatch.setenv("PAPERLESS_INDRA_MODE", "ocr")
    monkeypatch.setenv("PAPERLESS_INDRA_CONTENT_FORMAT", "text")
    monkeypatch.setenv("PAPERLESS_INDRA_KEEP_FURNITURE", "yes")
    monkeypatch.setenv("PAPERLESS_INDRA_MATH", "1")
    monkeypatch.setenv("PAPERLESS_INDRA_MODEL_DIR", "/models/indra")
    s = IndraSettings.from_env()
    assert (s.enabled, s.score, s.threads, s.dpi, s.mode, s.content_format) == (
        False,
        25,
        6,
        300.0,
        "ocr",
        "text",
    )
    assert s.keep_furniture and s.math and s.model_dir == "/models/indra"


def test_threads_follow_paperless_and_model_dir_fallback(monkeypatch):
    monkeypatch.setenv("PAPERLESS_THREADS_PER_WORKER", "4")
    monkeypatch.setenv("INDRA_OCR_MODEL_DIR", "/usr/share/indra-ocr")
    s = IndraSettings.from_env()
    assert s.threads == 4
    assert s.model_dir == "/usr/share/indra-ocr"


def test_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("PAPERLESS_INDRA_MODE", "magic")
    monkeypatch.setenv("PAPERLESS_INDRA_THREADS", "0")
    monkeypatch.setenv("PAPERLESS_INDRA_DPI", "lots")
    monkeypatch.setenv("PAPERLESS_INDRA_PDFA", "maybe")
    s = IndraSettings.from_env()
    assert (s.mode, s.threads, s.dpi, s.pdfa) == ("auto", 2, 200.0, True)


def test_converter_key_changes_with_engine_settings(monkeypatch):
    a = IndraSettings.from_env().converter_key()
    monkeypatch.setenv("PAPERLESS_INDRA_CONTENT_FORMAT", "text")
    assert IndraSettings.from_env().converter_key() == a  # content format is parser-side
    monkeypatch.setenv("PAPERLESS_INDRA_DPI", "300")
    assert IndraSettings.from_env().converter_key() != a
