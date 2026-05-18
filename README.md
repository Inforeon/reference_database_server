# docsearch

Document metadata index and search engine for managing reference material (research papers, textbooks, etc.) as model context. Extracts text content from PDFs, DOCX files, and Markdown, stores metadata in SQLite with FTS5 full-text search, and provides both a CLI and a REST API.

## Features

- **Multi-format extraction** — PDF (PyMuPDF), DOCX (python-docx), Markdown/Text (PyYAML frontmatter)
- **Full-text search** — SQLite FTS5 with filters on scope, file type, author, tags, date range, and document type
- **Document types** — First-class support for generic documents, research papers (with BibTeX generation via pdf2bib), textbooks (with chapter-level indexing), and references (metadata-only entries without associated files)
- **Sidecar metadata** — Editable `<file>.meta.json` files for tagging and annotation without modifying source files
- **Two interfaces** — Click-based CLI for local workflows, FastAPI REST API for remote access
- **Content change detection** — SHA-256 hashing avoids unnecessary re-indexing

## Installation

```bash
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

### CLI

```bash
# Scan a directory of papers
docsearch index scan ./papers -t paper

# Search across all indexed documents
docsearch search -q "transformer attention"

# Add a single paper with DOI embedding
docsearch papers add ./new_paper.pdf --doi 10.1234/example

# Export BibTeX
docsearch bibtex 42

# Tag a document via sidecar metadata
docsearch meta set ./papers/survey.pdf -k tag -v nlp
```

### REST API

```bash
# Start the server (defaults to 0.0.0.0:8000)
docsearch-server

# Or programmatically
uvicorn docsearch.server.app:app --host 0.0.0.0 --port 8000
```

Set `DOCSEARCH_HOME` to control the database home directory (default: current working directory).
Set `DOCSEARCH_DB_PATH` to place the SQLite database file anywhere (default: `{home}/docsearch.db`).

Interactive API docs available at `http://localhost:8000/docs`.

## Configuration

| | Default | Override |
|---|---|---|
| CLI home | Current working directory | `--home PATH` |
| API home | Current working directory | `DOCSEARCH_HOME` env var |
| Database path | `{home}/docsearch.db` | `DOCSEARCH_DB_PATH` env var |

All document paths are stored **relative** to the database home, making the index portable across machines. The database file itself can live independently (e.g. on local disk when home is a network mount).

## Document Types

| Type | Description |
|---|---|
| `generic` | Standard extract-and-index (default) |
| `paper` | Research papers with pdf2bib bibliographic extraction, DOI embedding, title validation, and BibTeX export |
| `textbook` | Textbooks split into chapters via TOC detection, each chapter indexed independently |
| `reference` | Metadata-only paper entries without an associated file (BibTeX auto-generated) |

## Source Types

Documents have a `source_type` indicating how they originate:

| Source Type | Applies To | Description |
|---|---|---|
| `file` | All types | Document backed by a file on disk (default) |
| `directory` | Textbooks only | Directory-based textbook with one file per chapter |
| `reference` | Papers only | Metadata-only entry with no associated file |

## Supported File Formats

| Format | Metadata Source |
|---|---|
| PDF | PyMuPDF (title, author, subject, creator, producer, dates, page count) |
| DOCX | python-docx custom properties (title, author, subject, keywords, comments, dates) |
| Markdown / Text | PyYAML frontmatter (everything between leading `---` delimiters) |

## CLI Reference

```
docsearch [--home PATH] COMMAND
```

### Commands

| Command | Description |
|---|---|
| `info` | Show database location and index statistics |

### Index Management

| Command | Description |
|---|---|
| `index scan <DIR>` | Scan directory tree and sync index (`-t TYPE`, `-r/--recursive`) |
| `index add <FILE>` | Add a single file (`-t TYPE`) |
| `index remove <FILE>` | Remove a file from the index |
| `index status <FILE>` | Check if a file needs re-indexing |

### Search

```
docsearch search -q QUERY [OPTIONS]
```

| Option | Description |
|---|---|
| `-q, --query TEXT` | Search query (required) |
| `--scope DIRECTORY` | Limit to subdirectory |
| `--type EXTENSION` | Filter by file extension |
| `--author NAME` | Filter by author |
| `--tag TAG` | Filter by tag (repeatable) |
| `--after DATE` | Indexed after date |
| `--before DATE` | Indexed before date |
| `--types TYPES` | Comma-separated document types |
| `--limit N` | Max results |
| `--offset N` | Pagination offset |
| `-f FORMAT` | Output: `text`, `json`, or `csv` (default: `text`) |

### Document Retrieval

| Command | Description |
|---|---|
| `get <DOC_ID>` | Retrieve extracted text (`-f text\|json`) |
| `bibtex <DOC_ID>` | Export BibTeX entry (papers only) |

### Sidecar Metadata

| Command | Description |
|---|---|
| `meta show <FILE>` | Display sidecar metadata |
| `meta set <FILE>` | Set a key/value (`-k KEY -v VALUE`) |
| `meta delete <FILE>` | Delete a key (`-k KEY`) |
| `meta init <FILE>` | Create empty sidecar file |

### Papers

| Command | Description |
|---|---|
| `papers add <FILE>` | Add research paper (`--doi`, `--skip-bib`, `-m KEY=VALUE`) |
| `papers upload <FILE>` | Upload and auto-index (`-n NAME`, `-D DIR`, `--doi`, `--skip-bib`, `-m KEY=VALUE`) |

### Textbooks

| Command | Description |
|---|---|
| `textbooks add <FILE>` | Add textbook (`-m KEY=VALUE`) |
| `textbooks upload <FILE>` | Upload and auto-index (`-n NAME`, `-D DIR`, `-m KEY=VALUE`) |
| `textbooks chapters <FILE>` | List indexed chapters |
| `textbooks chapter <FILE>` | Print chapter text (`-i CHAPTER_INDEX`) |

## REST API Reference

All routes prefixed with `/api`.

### Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check (`{status, home, db}`) |

### Index

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/index/scan` | Scan directory body: `{dirpath, recursive, document_type, extra_metadata}` → `{added, updated, removed, skipped, errors}` |
| `POST` | `/index/add` | Add file body: `{filepath, document_type, extra_metadata}` → `{id, path, filename, document_type}` |
| `POST` | `/index/remove` | Remove file body: `{filepath}` → `{removed}` |
| `POST` | `/index/upload` | Upload + auto-index (multipart file, query: `directory`, `filename`, `document_type`, `extra_metadata`) → `{id, path, filename}` |

### Search

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/search` | Full-text search (query params: `q`, `scope`, `file_type`, `author`, `tags`, `after`, `before`, `document_types`, `offset`, `limit`) → `{documents: {results, total}, chapters: {results, total}}` |

### Filesystem Browsing

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/fs` | List indexed contents of a directory (query param: `path` relative to db home) → `{path, entries, directories}` |

The `entries` array contains file-level documents (`type: "file"`, with `document_id`). The `directories` array contains inferred subdirectories (`type: "directory"`, no `document_id`) **and** directory-type textbooks (`type: "directory"`, **with** `document_id`). Files and directories are returned separately; path traversal outside the database home is rejected with 400.

### Documents

All document operations (metadata, content, file download, sidecar, BibTeX, move) apply to any document type — generic, paper, textbook, or reference.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/documents/{id}` | Get document metadata |
| `GET` | `/documents/{id}/content` | Get extracted text (`{id, path, filename, content}`) |
| `GET` | `/documents/{id}/file` | Download original file (binary `FileResponse`; 404 for references) |
| `GET` | `/documents/{id}/meta` | Get sidecar metadata |
| `PATCH` | `/documents/{id}/meta` | Update sidecar key body: `{key, value}` → `{updated, key}` |
| `GET` | `/documents/{id}/bibtex` | Export BibTeX (papers only, 400 if not paper) |
| `POST` | `/documents/{id}/move` | Move document body: `{destination}` → `{id, old_path, new_path, filename}` |
| `GET` | `/documents/{id}/chapters` | List textbook chapters (textbooks only, 400 if not textbook) |
| `GET` | `/documents/{id}/chapters/{index}` | Get chapter by index (textbooks only) → `{id, textbook_id, chapter_index, title, start_page, end_page, metadata, full_text}` |

### Papers

Paper-specific endpoints nested under `/documents`.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents/papers/add` | Add paper body: `{filepath, doi, skip_bib, extra_metadata}` → `{id, path, filename}` |
| `POST` | `/documents/papers/upload` | Upload paper (multipart, query: `doi`, `skip_bib`, `extra_metadata`, `directory`, `filename`) → `{id, path, filename}` |
| `POST` | `/documents/papers/reference` | Register metadata-only reference body: `{title, author, year, journal, booktitle, doi, url, bibtex, citation_key, extra_metadata}` → `{id, path, filename}` |

### Textbooks

Textbook-specific endpoints nested under `/documents`.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents/textbooks/add` | Add textbook body: `{filepath, extra_metadata}` → `{id, path, filename}` |
| `POST` | `/documents/textbooks/upload` | Upload textbook (multipart, query: `extra_metadata`, `directory`, `filename`, `variant`) → `{id, path, filename}` |
| `POST` | `/documents/{id}/chapters/upload` | Upload chapter file to directory-type textbook (multipart, query: `filename`, `chapter_index`) → chapter metadata |

## Architecture

```
docsearch/
├── config.py        — Central Config (database home, db path resolution)
├── core/            — Data models, SQLite repository, indexer, handlers
│   ├── models.py    — Document, Chapter, SearchResult, SearchQuery
│   ├── repository.py — SQLite + FTS5 repository
│   ├── indexer.py   — Directory scanning, file add/remove
│   └── handlers.py  — DocumentHandler pipeline (generic, paper, textbook, reference)
├── extractors/      — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/             — Click-based CLI commands
└── server/          — FastAPI REST API
    ├── app.py       — App factory, lifespan, health endpoint
    ├── schemas.py   — Pydantic request/response schemas
    └── routes/      — Route modules (documents, index, search, papers, textbooks)
```

Both CLI and API share the same `Repository`, `Indexer`, and `DocumentHandler` classes from `core/`.

## Testing

```bash
pytest
```

## Dependencies

- **Runtime:** click, fastapi, uvicorn, python-multipart, pymupdf, python-docx, pyyaml, pydantic, pdf2bib
- **Dev:** pytest, pytest-asyncio, httpx, mypy
