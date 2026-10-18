#!/usr/bin/env bash
# Install the paperless-indra parser plugin at container start.
#
# Mount this directory to /custom-cont-init.d (read-only is fine). The script
# runs as root before Paperless starts and installs, in this order:
#   1. a wheel placed next to it (paperless_indra-*.whl), or
#   2. PAPERLESS_INDRA_PIP_SPEC, e.g.
#      git+https://github.com/Pacman1984/paperless-ngx-indra-plugin.git@v0.1.0
# plus indra-ocr and its runtime deps (onnxruntime, opencv-python-headless,
# huggingface_hub). If PAPERLESS_INDRA_MODEL_DIR is set (e.g. a folder in the
# data volume) the models are downloaded there once, so later starts and all
# OCR work offline. Idempotent across restarts.
set -euo pipefail

log() { echo "[paperless-indra-init] $*"; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
wheel="$(ls -1 "${script_dir}"/paperless_indra-*.whl 2>/dev/null | sort -V | tail -n1 || true)"
spec="${wheel:-${PAPERLESS_INDRA_PIP_SPEC:-}}"
indra_version="${PAPERLESS_INDRA_OCR_VERSION:-0.3.2}"
py="$(command -v python3)"

if [[ -z "${spec}" ]]; then
    log "No wheel in ${script_dir} and PAPERLESS_INDRA_PIP_SPEC unset - skipping."
    exit 0
fi

pip_install() {
    if command -v uv >/dev/null 2>&1; then
        uv pip install --system --no-cache --python "${py}" "$@"
    else
        python3 -m pip install --no-cache-dir "$@"
    fi
}

installed="$(python3 -c 'import paperless_indra as m; print(m.__version__)' 2>/dev/null || true)"
if [[ -n "${wheel}" && -n "${installed}" && "$(basename "${wheel}")" == paperless_indra-"${installed}"-* ]]; then
    log "paperless-indra ${installed} already installed."
else
    log "Installing indra-ocr ${indra_version} and ${spec}"
    pip_install "indra-ocr==${indra_version}"
    pip_install --no-deps "${spec}"
    log "Installed paperless-indra $(python3 -c 'import paperless_indra as m; print(m.__version__)')"
fi

model_dir="${PAPERLESS_INDRA_MODEL_DIR:-}"
if [[ -n "${model_dir}" ]]; then
    if [[ -f "${model_dir}/rec.onnx" && -f "${model_dir}/meta.json" ]]; then
        log "Indra models present in ${model_dir}."
    else
        log "Downloading Indra models to ${model_dir}"
        mkdir -p "${model_dir}"
        python3 - "${model_dir}" <<'PY'
import sys
from huggingface_hub import snapshot_download
from indra_ocr.models import HF_REPO, HF_REVISION, MATH_FILES

snapshot_download(
    HF_REPO,
    revision=HF_REVISION,
    local_dir=sys.argv[1],
    allow_patterns=["*.onnx", "meta.json", "config.json", "LICENSE", "README.md"],
    ignore_patterns=list(MATH_FILES),
)
print("[paperless-indra-init] models", HF_REVISION)
PY
        # Paperless runs as USERMAP_UID; make the folder readable for it.
        chown -R "${USERMAP_UID:-1000}:${USERMAP_GID:-1000}" "${model_dir}" 2>/dev/null || true
    fi
else
    log "PAPERLESS_INDRA_MODEL_DIR unset: models are downloaded on first OCR (needs internet)."
fi
