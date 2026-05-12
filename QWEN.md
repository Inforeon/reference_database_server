# docsearch

Document metadata index and search engine for managing reference material (research papers, textbooks, etc.) as model context. Provides both a CLI and a REST API for indexing, searching, and querying files by content and metadata.

## Architecture

```
docsearch/
├── config.py        — Central Config class (database home, db path resolution)
├── core/            — Data models, SQLite repository, indexer, handlers
│   ├── models.py    — Document, SearchResult, SearchQuery dataclasses
│   ├── repository.py — SQLite + FTS5 repository
│   ├── indexer.py   — Directory scanning, file add/remove (delegates to handlers)
│   └── handlers.py  — DocumentHandler pipeline (generic, paper, textbook)
├── extractors/      — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/             — Click-based CLI commands
│   └── commands/    — index, search, get, meta, bibtex, papers, textbooks
└── server/          — FastAPI REST API
    ├── app.py       — App factory, lifespan, health endpoint
    ├── dependencies.py — Shared FastAPI dependencies (get_config)
    ├── schemas.py   — Pydantic request/response schemas
    └── routes/      — Route modules (documents, index, search, papers, textbooks)
```

**Shared core layer:** Both the CLI and REST API use the same `Repository`, `Indexer`, and `DocumentHandler` classes from `core/`, ensuring consistency between interfaces.

## Configuration

### Database Home

The **database home** is an explicit root directory under which all data lives:
- The SQLite database sits at `{home}/docsearch.db`
- All document paths are resolved relative to the database home
- File uploads are scoped within the database home

| | Default | Override |
|---|---|---|
| CLI | Current working directory (`.`) | `--home PATH` |
| REST API | Current working directory (`.`) | `DOCSEARCH_HOME` env var |

See `docsearch/config.py` — the `Config` class owns this logic.

## Document Types

The system supports three document types, distinguished by the `document_type` column in the database:

| Type | Handler | Behavior |
|---|---|---|
| `generic` (default) | `GenericDocumentHandler` | Standard extract-and-index via file-type extractors |
| `paper` | `PaperDocumentHandler` | Research papers with pdf2bib bibliographic extraction, DOI embedding, title validation |
| `textbook` | `TextbookDocumentHandler` | Splits PDF into chapters via TOC detection, stores each chapter independently in `textbook_chapters` table |

The `Indexer.add_file()` and `Indexer.scan_directory()` methods accept a `document_type` parameter and delegate to the appropriate handler via `get_handler()`.

## Key Components

### Core (`docsearch/core/`)

- **`models.py`** — `Document` dataclass (indexed document with extracted + sidecar metadata, `document_type`), `Chapter` (textbook chapter with `textbook_id`, `chapter_index`, `title`, `start_page`, `end_page`, `metadata`, `full_text`; `combined_metadata()` inherits from parent `Document`), `SearchResult` (document + FTS score + optional `chapter` field + snippet), `SearchQuery` (search parameters: query string, scope, file type, author, tags, date range, `document_types` filter, pagination). `from_row()` supports `tuple`, `dict`, and `sqlite3.Row` inputs (uses `row.keys()` membership checks for mapping types).
- **`repository.py`** — SQLite-backed store with WAL journal mode. Uses FTS5 for full-text search over `filename`, `directory`, and `full_text`. Dynamic SQL query builder supporting filters on scope, extension, author (via `json_extract`), tags, date range, and `document_types`. Methods: `upsert`, `search`, `get`, `get_by_id`, `remove`, `count`, `all_paths`, `exists`. Chapter methods: `upsert_chapter`, `get_chapters`, `get_chapter`, `delete_chapters`, `search_textbook_chapters` (two-phase: resolve textbook IDs from metadata filters → FTS search within those chapters)
- **`indexer.py`** — Orchestrates indexing. Calls `load_extractors()` from the extractors package to build the extension→extractor map. Delegates document processing to `handlers.get_handler()`. Computes SHA-256 content hashes for change detection, loads `.meta.json` sidecar files. `scan_directory()` performs a full sync: detects new/changed/deleted files and updates the index accordingly
- **`handlers.py`** — Pipeline-based document processing framework. Base `DocumentHandler` class with `pre_process()`, `extract_metadata()`, `extract_text()`, `post_process()` hooks. Subclasses:
  - `GenericDocumentHandler` — default behavior, identical to legacy indexer
  - `PaperDocumentHandler` — embeds DOI via `pdf2doi`, runs pdf2bib for bibliographic metadata, validates title match between PDF metadata and extracted citation (title mismatch guard), moves pdf2bib author list to `authors_bib` key, stores raw bibtex string in sidecar. Falls back to `_generate_bibtex_from_metadata()` when `skip_bib=True`
  - `TextbookDocumentHandler` — placeholder, currently behaves like generic
  - Helper functions: `_normalize_title()`, `_titles_match()`, `_format_author_dict()`, `_format_authors_bib()`, `_generate_bibtex_from_metadata()`

### Extractors (`docsearch/extractors/`)

All extend `BaseExtractor` (abstract: `supported_extensions`, `extract_metadata()`, `extract_text()`). Fault-tolerant — return empty results on failure rather than raising.

The extractors package owns knowledge of available extractors via `load_extractors()` in `__init__.py`, which returns an `extension → BaseExtractor` dict. The `Indexer` calls this function at init — adding a new extractor only requires editing `extractors/__init__.py`.

| Extractor | Extensions | Metadata Source |
|---|---|---|
| `PdfExtractor` | `pdf` | PyMuPDF (title, author, subject, creator, producer, dates, page count) |
| `DocxExtractor` | `docx` | python-docx custom properties (title, author, subject, keywords, comments, dates) |
| `MarkdownExtractor` | `md`, `markdown`, `txt` | PyYAML frontmatter (everything between leading `---` delimiters) |

### Sidecar Metadata

User-editable metadata lives in `<filepath>.meta.json` alongside source documents. The indexer reads these and stores them separately from extractor-derived metadata. The `combined_metadata` property merges both (sidecar overrides extracted). This allows manual tagging/annotation without modifying source files.

For papers, the sidecar also stores raw bibtex and parsed bibliographic metadata from pdf2bib.

### Database Schema

SQLite database with:
- **`documents`** table: path (unique key), filename, directory, extension, size, mtime, content_hash (SHA-256), extracted_metadata (JSON), sidecar_metadata (JSON), full_text, indexed_at, document_type (TEXT, default `'generic'`)
- **`documents_fts`**: FTS5 virtual table on (filename, directory, full_text) with unicode61 tokenizer, auto-synced via triggers
- Indexes on `directory` and `extension` columns
- Database file at `{home}/docsearch.db`

Migrations in `migrations/`:
- `001_initial.sql` — original schema (without `document_type`)
- `002_add_document_type.sql` — added `document_type` column

Migration SQL files are reference-only; the schema is embedded directly in `repository.py`'s `_SCHEMA_SQL`.

## CLI

Entry point: `docsearch` (maps to `docsearch.cli.main:cli`)

```
docsearch [--home PATH] COMMAND

Commands:
  info                Show database location and index statistics
  index scan <DIR>    Scan directory tree and sync index
  index add <FILE>    Add a single file to the index
  index remove <FILE> Remove a file from the index
  index status <FILE> Check if a file needs re-indexing
  search              Search indexed documents
  get <DOC_ID>        Retrieve extracted text content (-f text/json)
  meta show <FILE>    Display sidecar metadata
  meta set <FILE>     Set a key/value in sidecar (-k KEY -v VALUE)
  meta delete <FILE>  Delete a key from sidecar (-k KEY)
  meta init <FILE>    Create empty sidecar file
  bibtex <DOC_ID>     Export BibTeX for a paper
  papers add <FILE>   Add a research paper (--doi, --skip-bib, -m KEY=VALUE)
  papers upload <FILE> Upload a paper (--doi, --skip-bib, -n NAME, -D DIRECTORY)
  textbooks add <FILE>    Add a textbook (-m KEY=VALUE)
  textbooks upload <FILE> Upload a textbook (-n NAME, -D DIRECTORY)
```

Search supports: `-q QUERY`, `--scope DIR`, `--type EXT`, `--author NAME`, `--tag TAG` (repeatable), `--after/--before DATE`, `--limit N`, `--offset N`, `-f FORMAT` (text/json/csv). All output formats include the document `id`.

## REST API

Entry point: `docsearch-server` (maps to `docsearch.server.app:main`, starts uvicorn on `0.0.0.0:8000`)

All routes share a single `get_config()` dependency from `server/dependencies.py` that resolves the `Config` once at startup.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check (returns home and db path) |
| GET | `/api/search` | Search (query params: q, scope, file_type, author, tags, after, before, offset, limit) |
| POST | `/api/index/scan` | Scan directory body: `{dirpath, recursive, document_type, extra_metadata}` |
| POST | `/api/index/add` | Add file body: `{filepath, document_type, extra_metadata}` |
| POST | `/api/index/remove` | Remove file body: `{filepath}` |
| POST | `/api/index/upload` | Upload + auto-index (multipart, `directory`/`filename` query params) |
| GET | `/api/documents/{id}` | Get document metadata by ID |
| GET | `/api/documents/{id}/content` | Get extracted text content (`ContentResponse`) |
| GET | `/api/documents/{id}/file` | Download original file from disk (`FileResponse`) |
| GET | `/api/documents/{id}/meta` | Get sidecar metadata |
| PATCH | `/api/documents/{id}/meta` | Update sidecar key body: `{key, value}` |
| GET | `/api/documents/{id}/bibtex` | Export BibTeX (papers only, 400 if not paper type) |
| POST | `/api/papers/add` | Add paper body: `{filepath, doi?, skip_bib?, extra_metadata?}` |
| POST | `/api/papers/upload` | Upload paper (multipart, query: `doi`, `skip_bib`, `extra_metadata`, `directory`, `filename`) |
| POST | `/api/textbooks/add` | Add textbook body: `{filepath, extra_metadata?}` |
| POST | `/api/textbooks/upload` | Upload textbook (multipart, query: `extra_metadata`, `directory`, `filename`) |

Pydantic request/response schemas in `server/schemas.py`. `DocumentResponse` includes `id` and `document_type`. Upload endpoints save files relative to the database home with strict path-traversal protection.

## Tests

Located in `tests/`, run with `pytest`.

| File | Coverage |
|---|---|
| `test_repository.py` | `Document` model (`combined_metadata`, `from_row`), `Repository` (upsert, remove, count, all_paths, search with FTS/scope/author/extension/tags/limit filters, get_by_id, exists) |
| `test_extractors.py` | `PdfExtractor` (metadata extraction, text extraction, multi-page, fault tolerance) |
| `test_handlers.py` | BibTeX helpers (`_normalize_title`, `_titles_match`, `_format_author_dict`, `_format_authors_bib`, `_generate_bibtex_from_metadata`), `PaperDocumentHandler` integration (skip_bib, DOI embedding, title mismatch logic, authors_bib handling) |
| `test_server.py` | REST API content/file endpoints (`/content`, `/file`), upload (basic, subdirectory, custom name, path traversal rejection, nonexistent dir, unsupported type), BibTeX endpoint, paper endpoints (add/upload with DOI), textbook endpoints (add/upload) |
| `conftest.py` | Shared fixtures: `sample_pdf_with_metadata`, `sample_pdf_no_metadata`, `sample_pdf_multipage` (generated on-the-fly via PyMuPDF) |
| `fixtures/documents.py` | Duplicate of conftest.py fixtures (not currently imported) |

### Test Coverage Gaps

- No tests for `DocxExtractor` or `MarkdownExtractor`
- No tests for CLI commands (Click testing)
- No tests for `TextbookDocumentHandler`
- No tests for `Config` class
- No tests for `Indexer.scan_directory()`
- No tests for search route or sidecar CRUD operations

## Dependencies

- **Runtime:** click, fastapi, uvicorn, python-multipart, pymupdf, python-docx, pyyaml, pydantic, pdf2bib
- **Dev:** pytest, pytest-asyncio, httpx, mypy (strict mode)

## Conventions

- All extractors are fault-tolerant (catch exceptions, return empty results)
- Content-hash-based change detection avoids unnecessary re-indexing
- Extractor registry is owned by `extractors/__init__.py` (`load_extractors()`)
- Handler registry is owned by `core/handlers.py` (`get_handler()`)
- Database home is explicit; all paths resolve relative to it (`Config` class)
- Single shared `get_config()` dependency across all server routes
- mypy strict mode enabled; ignore missing imports
- Migration SQL in `migrations/` (schema also embedded in `repository.py`)
- Test PDFs are generated programmatically (no binary fixtures committed to repo)

## Known Limitations

- **Snippet support:** `SearchResult` has a `snippet` field but it is never populated (always empty string)
- **Textbook chunking:** `TextbookDocumentHandler` is a placeholder; chapter/section splitting is planned but not implemented
- **Migration runner:** Migration files exist but are reference-only; no automated migration execution
