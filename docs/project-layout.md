# Project Layout

The repository uses a `src/` layout. Runtime code is installed/imported as the
`cyber_doctor` package. The structured pathogen knowledge base is runtime data,
not application source, and lives at the repository root beside `src/`.

```text
cyber-doctor/
├── docs/
│   ├── development.md      # Local setup and script groups
│   └── project-layout.md   # Source and runtime data boundaries
├── scripts/
│   ├── data/               # Cleaning, chunking, catalog and ingestion tools
│   ├── diagnostics/        # Retrieval inspection and data validation
│   ├── ops/                # UpToDate browser and batch utilities
│   └── reports/            # Standalone report builders
├── structured/             # DGX runtime knowledge base: structured pathogen JSON
├── src/
│   └── cyber_doctor/
│       ├── client/         # LLM API clients
│       ├── config/         # Packaged YAML configuration
│       ├── internet/       # Web search pipeline
│       ├── kg/             # Graph database adapter
│       ├── mngs/           # Pathogen parsing, retrieval and judgement
│       ├── model/          # KG and RAG implementations
│       ├── qa/             # Question routing and tools
│       ├── rag/            # General retrieval-augmented generation
│       ├── reporting/      # Explainability report mapping and PDF renderer
│       └── resources/      # UI images
├── tests/
├── outputs/                # Runtime PDFs; ignored by Git
├── app.py                  # Backward-compatible launcher
├── pyproject.toml
└── requirements.txt
```

## Data Boundaries

- Structured article JSON and five-dimension pathogen archives are runtime
  data. Store them outside the source checkout and configure
  `STRUCTURED_KNOWLEDGE_ROOT` and `FIVE_DIMENSION_KNOWLEDGE_ROOT` with the
  deployment's own paths. These data are ignored by Git and are not committed
  with source code.
- `.env`, other user knowledge bases, vector-store data, caches and logs are
  runtime state and are not committed. On a developer machine, the structured
  knowledge root may instead point to a separately managed local data folder.
- `src/cyber_doctor/mngs/pathogen_catalog.json` is a small application lookup
  resource and is intentionally packaged with the code.
- UpToDate and medical-book processing scripts accept explicit input/output
  locations; their article corpora are not bundled with the source package.
- The root `app.py` remains available for the existing `python app.py` workflow.
