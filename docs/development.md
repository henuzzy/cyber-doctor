# Development

## Setup

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m unittest discover -s tests -v
```

Start the Gradio application from the repository root with:

```bash
python app.py
```

## Script Groups

- `scripts/data/` prepares Markdown corpora, builds pathogen catalogs and
  manages offline Milvus ingestion.
- `scripts/diagnostics/` inspects mNGS retrieval and normalizes/evaluates
  captured inputs.
- `scripts/ops/` contains UpToDate browser automation and batch collection.
- `scripts/reports/` contains standalone, task-specific report builders.

Keep generated output under `outputs/` or a caller-provided external path.
Do not commit credentials, patient data, article corpora, generated reports or
local browser profiles.

The mNGS structured pathogen JSON repository is runtime data under the project
root's `structured/` directory on DGX. Set `STRUCTURED_KNOWLEDGE_ROOT` to that
directory; it is intentionally Git-ignored and must be provisioned separately
from the source checkout.
