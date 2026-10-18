# Paperless-ngx with the Indra OCR parser plugin and its models baked in.
#
# Build from a local checkout (context = repository root):
#   docker build -f docker/Dockerfile -t paperless-ngx-indra:3.3.1 .
#
# Pin PAPERLESS_TAG to the version you run; the plugin needs >= 3.0.
# The Indra model weights are licensed separately (modified OpenRAIL-M, see
# README "Licenses"). Don't push the resulting image to a public registry
# without passing that license on.
ARG PAPERLESS_TAG=3.3.1
FROM ghcr.io/paperless-ngx/paperless-ngx:${PAPERLESS_TAG}

ARG INDRA_OCR_VERSION=0.3.2
ENV INDRA_OCR_MODEL_DIR=/usr/share/indra-ocr

COPY pyproject.toml README.md LICENSE /tmp/paperless-indra/
COPY paperless_indra /tmp/paperless-indra/paperless_indra

# 1. Install indra-ocr with its runtime deps (onnxruntime, opencv-python-headless,
#    huggingface_hub), then the plugin itself with --no-deps.
# 2. Fail the build if any package Paperless already had changed version.
# 3. Download the model revision this indra-ocr release was tested against
#    (without the optional math models) so nothing is fetched at runtime.
RUN set -eu; \
    py="$(command -v python3)"; \
    uv pip freeze --python "$py" | sort > /tmp/before.txt; \
    uv pip install --system --no-cache --python "$py" "indra-ocr==${INDRA_OCR_VERSION}"; \
    uv pip install --system --no-cache --no-deps --python "$py" /tmp/paperless-indra; \
    uv pip freeze --python "$py" | sort > /tmp/after.txt; \
    changed="$(comm -23 /tmp/before.txt /tmp/after.txt)"; \
    if [ -n "$changed" ]; then echo "Paperless dependencies changed:"; echo "$changed"; exit 1; fi; \
    echo "Added packages:"; comm -13 /tmp/before.txt /tmp/after.txt; \
    python3 -c "from huggingface_hub import snapshot_download; from indra_ocr.models import HF_REPO, HF_REVISION, MATH_FILES; snapshot_download(HF_REPO, revision=HF_REVISION, local_dir='${INDRA_OCR_MODEL_DIR}', allow_patterns=['*.onnx', 'meta.json', 'config.json', 'LICENSE', 'README.md'], ignore_patterns=list(MATH_FILES)); print('Indra models', HF_REVISION)"; \
    rm -rf "${INDRA_OCR_MODEL_DIR}/.cache" /tmp/paperless-indra /tmp/before.txt /tmp/after.txt; \
    python3 -c "import indra_ocr, paperless_indra; c = indra_ocr.load(threads=1); print('paperless-indra', paperless_indra.__version__, 'indra-ocr', indra_ocr.__version__, 'models OK')"
