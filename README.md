# paperless-indra

A [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) **parser plugin** that replaces Tesseract
with [Indra OCR](https://github.com/datalab-to/indra) from Datalab. Indra is a small, fast OCR engine built
on ONNX Runtime that runs on the CPU. It runs **inside the Paperless container**, the way Tesseract does,
so no extra service or GPU is needed. The plugin writes a **searchable archive PDF with an invisible text
layer**, so search, highlighting and the archive version keep working in Paperless.

- **Local.** OCR runs in the Paperless worker. In the custom image the models (~18 MB) are baked in, so
  nothing is downloaded at runtime and no documents leave the host.
- **Fast.** Datalab reports ~230 ms per page on 4 CPU cores. In this repo's end-to-end test, an A4 scan
  took ~0.65 s in the Paperless container, and the models loaded in 0.65 s once per worker.
- **Layout-aware content.** Document content is Indra's Markdown (headings, lists, tables), or plain text.
- **Accurate text layer.** Invisible text is placed at Indra's line boxes. This includes scans that were
  fed in sideways or upside down: Indra turns them upright, and the plugin writes the text back in the
  printed direction. Pages that already have a text layer are left untouched.
- **Mixed PDFs.** With `PAPERLESS_INDRA_MODE=auto`, Indra reads a PDF's own text layer where it is sound
  and OCRs only the rest.
- **PDF/A archives.** Archive PDFs are converted to PDF/A following `PAPERLESS_OCR_OUTPUT_TYPE` (OCRmyPDF
  without an OCR engine, so Tesseract never runs).
- **No changes to Paperless.** It's configured only through optional `PAPERLESS_INDRA_*` variables, and
  Paperless core is not forked or patched.
- **Paperless 3.x and 2.x.** On 3.x the plugin loads through the `paperless_ngx.parsers` entry point. On
  2.x (tested with 2.20.15) you turn it on with one line, `PAPERLESS_APPS=paperless_indra.legacy`; see
  [Paperless-ngx 2.x](#paperless-ngx-2x).

## How it works

```
Paperless consumer ──► IndraDocumentParser.parse()
                         │ image? → img2pdf (local)
                         │ born-digital PDF & no archive wanted? → use embedded text, done
                         ▼
                       Indra (in-process, ONNX Runtime, cached per worker)
                         │ per page: PDF text layer where sound, OCR elsewhere
                         ▼
                       content  = Markdown (or plain text)
                       archive  = original pages + invisible text on OCR'd lines
                                → PDF/A via OCRmyPDF (ocr_engine=none) if OUTPUT_TYPE is pdfa*
```

The parser registers with score `15`. That beats the built-in Tesseract parser (`10`) and stays below
remote OCR parsers (`20`). It declares `uses_remote_service = False`, so Paperless always considers it,
whatever `PAPERLESS_REMOTE_OCR_MODE` is set to.

**Using it together with a remote engine** such as the
[MinerU plugin](https://github.com/Pacman1984/paperless-ngx-mineru-plugin) or Azure: set
`PAPERLESS_REMOTE_OCR_MODE=workflow_only`. Documents matched by a workflow with the **Remote OCR** action
then go to the remote engine, and everything else is OCR'd locally by Indra.

## Installation

### A. Custom Docker image (recommended)

The image includes the plugin, Indra's runtime deps (onnxruntime, opencv-python-headless,
huggingface_hub) and the model files of the revision the pinned `indra-ocr` release was tested against.

The new packages are installed with every package Paperless already ships pinned as a constraint, so only
packages are added and none are upgraded. As a second safety net, the build fails if any existing version
changes anyway.

From a local checkout:

```bash
docker build -f docker/Dockerfile --build-arg PAPERLESS_TAG=3.3.1 -t paperless-ngx-indra:3.3.1 .
```

Or straight from GitHub:

```bash
docker build -f docker/Dockerfile --build-arg PAPERLESS_TAG=3.3.1 \
  -t paperless-ngx-indra:3.3.1 \
  "https://github.com/Pacman1984/paperless-ngx-indra-plugin.git#main"
```

Then use `paperless-ngx-indra:3.3.1` as the image of your Paperless service. See
[`examples/docker-compose.override.yml`](examples/docker-compose.override.yml) (option A). The image is
about 245 MB larger than the stock Paperless image.

### B. Bootstrap script (stock image)

Copy [`docker/custom-cont-init.d/`](docker/custom-cont-init.d) next to your compose file and mount it to
`/custom-cont-init.d`. At container start the script installs `indra-ocr` and the plugin, from a wheel
placed in that folder or from `PAPERLESS_INDRA_PIP_SPEC`.

If you set `PAPERLESS_INDRA_MODEL_DIR` to a folder in the data volume, for example
`/usr/src/paperless/data/indra-ocr`, the models are downloaded there once and OCR works offline after
that. See option B in [`examples/docker-compose.override.yml`](examples/docker-compose.override.yml).

The script uses the same constraint approach as the image, so Paperless' own packages stay untouched.

To build a wheel:

```bash
uv build --wheel     # → dist/paperless_indra-<version>-py3-none-any.whl
```

### C. Bare-metal Paperless

Install into the Python environment Paperless runs in:

```bash
uv pip install --python /path/to/paperless/venv/bin/python \
  "git+https://github.com/Pacman1984/paperless-ngx-indra-plugin.git@main"
```

The models are downloaded from Hugging Face on first use. For offline use, point `INDRA_OCR_MODEL_DIR` at
a local copy.

## Paperless-ngx 2.x

Paperless 2.x has no parser entry point, but it can load extra Django apps. The plugin ships a small one,
`paperless_indra.legacy`. Like the built-in Tesseract parser, it registers through Paperless' parser
signal, and with weight 15 it wins over Tesseract's 0. This is tested end to end with **2.20.15**.

1. Build the image on your 2.x version, or use the bootstrap script on the stock 2.x image (options A/B
   above):

   ```bash
   docker build -f docker/Dockerfile --build-arg PAPERLESS_TAG=2.20.15 -t paperless-ngx-indra:2.20.15 .
   ```

2. Add one line to `docker-compose.env`:

   ```
   PAPERLESS_APPS=paperless_indra.legacy
   ```

   If `PAPERLESS_APPS` is already set, for example for social login providers, append it with a comma.

3. Restart. The log shows:

   ```
   Registered parser 'Paperless-ngx Indra OCR Parser' v0.1.0 for Paperless 2.x (weight 15).
   Indra OCR read scan.pdf: 1 page(s) (1 ocr)
   ```

On 2.x the 2.x archive settings apply: `PAPERLESS_OCR_SKIP_ARCHIVE_FILE` (`never`/`with_text`/`always`)
and `PAPERLESS_OCR_MODE=skip_noarchive`. Thumbnails, page counts and metadata come from Paperless' own
Tesseract parser class, which the 2.x parser extends, but Tesseract itself is never run.

Paperless 2.x bundles OCRmyPDF 16, which has no `ocr_engine=none` option yet. PDF/A conversion there
uses a small null-OCR-engine plugin shipped with this package (`paperless_indra.ocrmypdf_null`). The
archive's creator tag shows it, for example `OCRmyPDF (no OCR, paperless-indra)`.

**When you upgrade to 3.x,** remove `paperless_indra.legacy` from `PAPERLESS_APPS`; the same package then
loads through the 3.x entry point. Left in place, it only logs a hint and does nothing.

## Configuration

All settings are optional. A commented template is in
[`examples/docker-compose.env.example`](examples/docker-compose.env.example).

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAPERLESS_INDRA_ENABLED` | `true` | `false` lets Tesseract handle documents again, without uninstalling |
| `PAPERLESS_INDRA_SCORE` | `15` | Parser priority: > 10 beats Tesseract, < 20 lets remote OCR parsers win |
| `PAPERLESS_INDRA_THREADS` | `PAPERLESS_THREADS_PER_WORKER` (or 2) | ONNX Runtime threads per worker |
| `PAPERLESS_INDRA_DPI` | `200` | Render resolution for OCR |
| `PAPERLESS_INDRA_MODE` | `auto` | `auto` (PDF text layer where sound, OCR elsewhere), `ocr` (always OCR) or `text` |
| `PAPERLESS_INDRA_CONTENT_FORMAT` | `markdown` | `markdown` or `text` |
| `PAPERLESS_INDRA_TEXT_LAYER` | `true` | Write the invisible text layer and produce an archive PDF |
| `PAPERLESS_INDRA_PDFA` | `true` | Convert the archive to PDF/A when `PAPERLESS_OCR_OUTPUT_TYPE` is `pdfa*` |
| `PAPERLESS_INDRA_KEEP_FURNITURE` | `false` | Keep page headers and footers in the Markdown |
| `PAPERLESS_INDRA_MATH` | `false` | Formulas as LaTeX. Needs the extra math models, which the image doesn't include |
| `PAPERLESS_INDRA_MODEL_DIR` | `INDRA_OCR_MODEL_DIR` | Model folder. The custom image sets `/usr/share/indra-ocr` |

These Paperless settings keep their usual meaning:

- `PAPERLESS_ARCHIVE_FILE_GENERATION`: with `auto`, born-digital PDFs are not OCR'd.
- `PAPERLESS_OCR_OUTPUT_TYPE`: PDF/A or plain PDF.
- `PAPERLESS_OCR_IMAGE_DPI`: fallback DPI for images without DPI metadata.

`PAPERLESS_OCR_LANGUAGE` only affects Tesseract. Indra detects the script by itself.

Supported input types are PDF, PNG, JPEG, TIFF, BMP, GIF and WebP. Office files keep going through
Tika/Gotenberg.

## Verify the installation

1. Restart Paperless and check the log (`docker compose logs webserver`). Discovery is logged as:

   ```
   Loaded third-party parser 'Paperless-ngx Indra OCR Parser' v0.1.0 by paperless-indra contributors (entrypoint: 'indra').
   ```

2. Consume a scanned PDF. The log shows:

   ```
   Document handled by third-party parser 'Paperless-ngx Indra OCR Parser' v0.1.0 — https://github.com/...
   Loading Indra OCR 0.3.2 (threads=2, dpi=200, mode=auto)
   Indra OCR read scan.pdf: 1 page(s) (1 ocr)
   ```

3. In the Paperless UI, the document content is Indra's Markdown. If you search for a word, the archive
   PDF highlights it at the right spot.

If the discovery line is missing, check the install:

```bash
docker compose exec webserver python3 -c "import paperless_indra, indra_ocr; print(paperless_indra.__version__, indra_ocr.__version__)"
```

## Limitations

These limits are Indra's own, as listed by Datalab:

- **Scripts.** Scans in Latin (about 50 languages, German included), Cyrillic, Greek, Devanagari, Bengali
  and Chinese are OCR'd. Arabic, Hebrew, Thai, Japanese kana and Korean are only read from digital PDF
  text layers.
- **Content it handles poorly.** Handwriting, vertical text, and complex tables (merged cells, missing
  ruling lines).
- **Accuracy.** On OmniParseBench's text score Indra is well ahead of Tesseract 5 (41.3 vs 19.3), but
  VLM-based engines such as MinerU are stronger on hard layouts. Combine the two with
  `workflow_only` (see above) if you need both. That combination needs Paperless 3.x; 2.x has no remote OCR
  routing.
- **Ignored Paperless OCR settings.** `PAPERLESS_OCR_MODE` (`skip`/`redo`/`force`) and the
  deskew/rotate/clean settings only apply to Tesseract. Indra turns pages upright itself, and the archive
  keeps the original pages.

## Licenses

- **This plugin's code** is MIT licensed.
- **Indra's code** (`indra-ocr`) is Apache-2.0.
- **Indra's model weights** ([datalab-to/indra-ocr](https://huggingface.co/datalab-to/indra-ocr)) use a
  **modified OpenRAIL-M license**. Personal and research use is free, but there are restrictions for
  organisations above $20M revenue or funding and for competing products. Read the
  [license](https://huggingface.co/datalab-to/indra-ocr/blob/main/LICENSE) before commercial use.
- **The Docker image** built from `docker/Dockerfile` contains the weights and their LICENSE file
  (`/usr/share/indra-ocr/LICENSE`). Don't publish such an image without passing that license on.

## Development

```bash
uv sync                    # creates .venv (includes indra-ocr and onnxruntime)
uv run pytest              # unit tests; tests marked "models" download the ~18 MB models once
uv run pytest -m "not models"   # offline subset
uv run ruff check .
```

End-to-end test with Paperless in Docker (SQLite and Redis). The container runs on an internal-only
network, which proves the OCR needs no internet. The test also checks that the archive is PDF/A with a
searchable text layer and that OCRmyPDF ran without an OCR engine. It is a throwaway stack that is torn
down afterwards:

```bash
bash tests/e2e/run-e2e.sh            # Paperless 3.3.1 (entry point)
LEGACY=1 bash tests/e2e/run-e2e.sh   # Paperless 2.20.15 (PAPERLESS_APPS)
```
