#!/usr/bin/env bash
# Build Paperless-ngx + plugin (Indra models baked in), consume a synthetic
# scanned invoice offline and verify: plugin discovered, Indra parser used,
# content OCR'd, archive PDF/A with searchable text layer. Tears down afterwards
# (KEEP=1 keeps the stack for inspection).
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose --env-file .env -f docker-compose.yml)
touch .env  # optional overrides (PAPERLESS_TAG)
export PAPERLESS_SECRET_KEY="${PAPERLESS_SECRET_KEY:-$(uv run --project ../.. python -c 'import secrets; print(secrets.token_urlsafe(48))')}"

cleanup() {
    if [[ "${KEEP:-0}" != "1" ]]; then
        "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
        rm -rf consume
    fi
}
trap cleanup EXIT

mkdir -p consume
uv run --project ../.. python make_test_page.py consume/e2e-rechnung.pdf.tmp
"${compose[@]}" up -d --build

echo "== waiting for parser discovery"
for _ in $(seq 1 120); do
    if "${compose[@]}" logs paperless 2>&1 | grep -q "Loaded third-party parser 'Paperless-ngx Indra OCR Parser'"; then
        break
    fi
    sleep 2
done
"${compose[@]}" logs paperless 2>&1 | grep "third-party parser" || { echo "plugin NOT discovered"; exit 1; }

# Only drop the file once the consumer is up, so it is picked up exactly once.
mv consume/e2e-rechnung.pdf.tmp consume/e2e-rechnung.pdf

echo "== waiting for consumption"
for _ in $(seq 1 150); do
    logs="$("${compose[@]}" logs paperless 2>&1)"
    if grep -qE "Document .*e2e-rechnung.* consumption finished|consumption finished" <<<"$logs"; then
        break
    fi
    if grep -qE "Indra OCR failed|Error occurred while consuming|Unexpected error while consuming" <<<"$logs"; then
        grep -E "Indra|ERROR|Error" <<<"$logs" | tail -20
        exit 1
    fi
    sleep 2
done

"${compose[@]}" logs paperless 2>&1 | grep -E "third-party parser|Indra|Parser:" | tail -10
"${compose[@]}" logs paperless 2>&1 | grep -q "Document handled by third-party parser 'Paperless-ngx Indra OCR Parser'" \
    || { echo "document was NOT handled by the Indra parser"; exit 1; }

echo "== verifying document"
"${compose[@]}" exec -T paperless python3 manage.py shell -c '
import subprocess
from documents.models import Document
d = Document.objects.get(original_filename="e2e-rechnung.pdf")
print("content:", d.content[:200].replace("\n", " | "))
assert "Gesamtbetrag" in d.content, "OCR text missing"
assert d.has_archive_version, "no archive PDF"
txt = subprocess.run(["pdftotext", str(d.archive_path), "-"], capture_output=True, text=True).stdout
print("archive text layer:", " ".join(txt.split())[:200])
assert "1.234,56" in txt, "archive PDF has no searchable text layer"
import pikepdf
with pikepdf.open(d.archive_path) as pdf:
    meta = pdf.open_metadata()
    print("archive PDF/A:", meta.get("pdfaid:part"), meta.get("pdfaid:conformance"))
    assert meta.get("pdfaid:part"), "archive is not PDF/A"
print("E2E OK")
'
