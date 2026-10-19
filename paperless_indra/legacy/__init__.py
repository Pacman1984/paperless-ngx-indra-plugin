"""Paperless-ngx 2.x compatibility for the Indra parser.

Paperless 2.x has no ``paperless_ngx.parsers`` entry point. Instead, list this
package as an extra Django app::

    PAPERLESS_APPS=paperless_indra.legacy

Its ``AppConfig.ready()`` registers the parser through the
``document_consumer_declaration`` signal, the same way the built-in
Tesseract parser does. On Paperless 3.x the app does nothing (the entry point
is used there).
"""
