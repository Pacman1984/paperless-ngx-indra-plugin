#!/usr/bin/env bash
# Install the paperless-indra parser plugin at container start.
#
# Mount this directory to /custom-cont-init.d (read-only is fine). The script
# runs as root before Paperless starts and installs, in this order:
#   1. a wheel placed next to it (paperless_indra-*.whl), or
#   2. PAPERLESS_INDRA_PIP_SPEC, e.g.
#      git+https://github.com/Pacman1984/paperless-ngx-indra-plugin.git@main
# plus indra-ocr (PAPERLESS_INDRA_OCR_VERSION) and its runtime deps
# (onnxruntime, opencv-python-headless, huggingface_hub). If
# PAPERLESS_INDRA_MODEL_DIR is set (e.g. a folder in the data volume) the
# models of the installed indra-ocr release are downloaded there - again
# whenever that release expects a different model revision - so OCR works
# offline. Idempotent across restarts.
set -euo pipefail

log() { echo "[paperless-indra-init] $*"; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
wheel="$(ls -1 "${script_dir}"/paperless_indra-*.whl 2>/dev/null | sort -V | tail -n1 || true)"
spec="${wheel:-${PAPERLESS_INDRA_PIP_SPEC:-}}"
indra_version="${PAPERLESS_INDRA_OCR_VERSION:-1.1.1}"
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
installed_indra="$(python3 -c 'import indra_ocr as m; print(m.__version__)' 2>/dev/null || true)"
if [[ -n "${wheel}" && -n "${installed}" && "$(basename "${wheel}")" == paperless_indra-"${installed}"-* \
      && "${installed_indra}" == "${indra_version}" ]]; then
    log "paperless-indra ${installed} with indra-ocr ${installed_indra} already installed."
else
    log "Installing indra-ocr ${indra_version} and ${spec}"
    # Pin everything Paperless already ships, so only new packages are added.
    constraints="$(mktemp)"
    python3 -m pip freeze 2>/dev/null | grep -E '^[A-Za-z0-9_.-]+==' | grep -viE '^(indra-ocr|paperless-indra)==' > "${constraints}" \
        || uv pip freeze --python "${py}" | grep -E '^[A-Za-z0-9_.-]+==' | grep -viE '^(indra-ocr|paperless-indra)==' > "${constraints}"
    pip_install -c "${constraints}" "indra-ocr==${indra_version}"
    rm -f "${constraints}"
    pip_install --no-deps --reinstall "${spec}"
    log "Installed paperless-indra $(python3 -c 'import paperless_indra as m; print(m.__version__)')"
fi

model_dir="${PAPERLESS_INDRA_MODEL_DIR:-}"
if [[ -z "${model_dir}" ]]; then
    log "PAPERLESS_INDRA_MODEL_DIR unset: models are downloaded on first OCR (needs internet)."
    exit 0
fi

wanted="$(python3 -c 'from indra_ocr.models import HF_REVISION; print(HF_REVISION)')"
have="$(cat "${model_dir}/.revision" 2>/dev/null || true)"
if [[ "${have}" == "${wanted}" && -f "${model_dir}/rec.onnx" ]]; then
    log "Indra models ${wanted} present in ${model_dir}."
    exit 0
fi

log "Downloading Indra models ${wanted} to ${model_dir} (had: ${have:-none})"
tmp="${model_dir}.download"
rm -rf "${tmp}"
mkdir -p "${tmp}"
python3 - "${tmp}" <<'PY'
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from indra_ocr.models import HF_REPO, HF_REVISION, MATH_FILES

snapshot_download(
    HF_REPO,
    revision=HF_REVISION,
    local_dir=sys.argv[1],
    allow_patterns=["*.onnx", "meta.json", "config.json", "lm.npz", "lm_*.npz", "LICENSE", "README.md"],
    ignore_patterns=list(MATH_FILES),
)
Path(sys.argv[1], ".revision").write_text(HF_REVISION)
PY
rm -rf "${tmp}/.cache"
# Swap in the complete new set, so no file of an older revision lingers.
rm -rf "${model_dir}.old"
[[ -d "${model_dir}" ]] && mv "${model_dir}" "${model_dir}.old"
mv "${tmp}" "${model_dir}"
rm -rf "${model_dir}.old"
# Paperless runs as USERMAP_UID; make the folder readable for it.
chown -R "${USERMAP_UID:-1000}:${USERMAP_GID:-1000}" "${model_dir}" 2>/dev/null || true
log "Indra models ${wanted} ready."
