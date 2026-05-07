# docsearch

Document metadata index and search engine for managing reference material (research papers, textbooks, etc.) as model context. Provides both a CLI and a REST API for indexing, searching, and querying files by content and metadata.

## Architecture

```
docsearch/
├── core/          — Data models, SQLite repository, indexer
├── extractors/    — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/           — Click-based CLI commands
└── server/        — FastAPI REST API
```

**Shared core layer:** Both the CLI and REST API use the same `Repository` and `Indexer` classes from `core/`, ensuring consistency between interfaces.

## Key Components

### Core (`docsearch/core/`)

- **`models.py`** — `Document` dataclass (indexed document with extracted + sidecar metadata), `SearchResult` (document + FTS score), `SearchQuery` (search parameters: query string, scope, file type, author, tags, date range, pagination)
- **`repository.py`** — SQLite-backed store with WAL journal mode. Uses FTS5 for full-text search over `filename`, `directory`, and `full_text`. Dynamic SQL query builder supporting filters on scope, extension, author (via `json_extract`), tags, and date range. Methods: `upsert`, `search`, `get`, `get_by_id`, `remove`, `count`, `all_paths`
- **`indexer.py`** — Orchestrates indexing. Maps file extensions to extractors, computes SHA-256 content hashes for change detection, loads `.meta.json` sidecar files. `scan_directory()` performs a full sync: detects new/changed/deleted files and updates the index accordingly

### Extractors (`docsearch/extractors/`)

All extend `BaseExtractor` (abstract: `supported_extensions`, `extract_metadata()`, `extract_text()`). Fault-tolerant — return empty results on failure rather than raising.

| Extractor | Extensions | Metadata Source |
|---|---|---|
| `PdfExtractor` | `pdf` | PyMuPDF (title, author, subject, creator, producer, dates, page count) |
| `DocxExtractor` | `docx` | python-docx custom properties (title, author, subject, keywords, comments, dates) |
| `MarkdownExtractor` | `md`, `markdown`, `txt` | PyYAML frontmatter (everything between leading `---` delimiters) |

### Sidecar Metadata

User-editable metadata lives in `<filepath>.meta.json` alongside source documents. The indexer reads these and stores them separately from extractor-derived metadata. The `combined_metadata` property merges both (sidecar overrides extracted). This allows manual tagging/annotation without modifying source files.

### Database Schema

SQLite database with:
- **`documents`** table: path (unique key), filename, directory, extension, size, mtime, content_hash (SHA-256), extracted_metadata (JSON), sidecar_metadata (JSON), full_text, indexed_at
- **`documents_fts`**: FTS5 virtual table on (filename, directory, full_text) with unicode61 tokenizer, auto-synced via triggers
- Indexes on `directory` and `extension` columns
- Default location: `~/.local/share/docsearch/docsearch.db` (overridable via `--db` flag or `DOCSEARCH_DB` env var)

## CLI

Entry point: `docsearch` (maps to `docsearch.cli.main:cli`)

```
docsearch [--db PATH] COMMAND

Commands:
  info                Show database location and index statistics
  index scan <DIR>    Scan directory tree and sync index
  index add <FILE>    Add a single file to the index
  index remove <FILE> Remove a file from the index
  index status <FILE> Check if a file needs re-indexing
  search              Search indexed documents
  meta show <FILE>    Display sidecar metadata
  meta set <FILE>     Set a key/value in sidecar (-k KEY -v VALUE)
  meta delete <FILE>  Delete a key from sidecar (-k KEY)
  meta init <FILE>    Create empty sidecar file
```

Search supports: `-q QUERY`, `--scope DIR`, `--type EXT`, `--author NAME`, `--tag TAG` (repeatable), `--after/--before DATE`, `--limit N`, `--offset N`, `-f FORMAT` (text/json/csv)

## REST API

Entry point: `docsearch-server` (maps to `docsearch.server.app:main`, starts uvicorn on `0.0.0.0:8000`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/search` | Search (query params: q, scope, file_type, author, tags, after, before, offset, limit) |
| POST | `/api/index/scan` | Scan directory body: `{dirpath, recursive}` |
| POST | `/api/index/add` | Add file body: `{filepath}` |
| POST | `/api/index/remove` | Remove file body: `{filepath}` |
| GET | `/api/documents/{id}` | Get document by ID |
| GET | `/api/documents/{id}/meta` | Get sidecar metadata |
| PATCH | `/api/documents/{id}/meta` | Update sidecar key body: `{key, value}` |

Pydantic request/response schemas in `server/schemas.py`.

## Dependencies

- **Runtime:** click, fastapi, uvicorn, pymupdf, python-docx, pyyaml, pydantic
- **Dev:** pytest, pytest-asyncio, httpx, mypy (strict mode)

## Conventions

- All extractors are fault-tolerant (catch exceptions, return empty results)
- Content-hash-based change detection avoids unnecessary re-indexing
- mypy strict mode enabled; ignore missing imports
- Tests in `tests/`, currently covering `Repository` and `Document` models
- Migration SQL in `migrations/` (schema also embedded in `repository.py`)
