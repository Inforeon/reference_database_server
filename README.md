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

### CLI Path Resolution

CLI commands resolve user-supplied relative paths against the current working directory first, then validate that the result is within the database home. This means you can work naturally from subdirectories:

```bash
cd ~/docs/home/proj_1
docsearch papers add paper.pdf    # indexes as proj_1/paper.pdf
docsearch index move paper.pdf ../proj_2  # moves into proj_2, keeping name
```

Absolute paths are also accepted but must reside within the database home. Paths outside the home produce a clear error message.

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
| `info [DOC_ID]` | Show database location and index statistics; with DOC_ID, show full document metadata |

### Index Management

| Command | Description |
|---|---|
| `index scan <DIR>` | Scan directory tree and sync index (`-T/--document-type TYPE`, `--no-recursive`) |
| `index add <FILE>` | Add a single generic file to the index |
| `index remove <FILE>` | Remove a file from the index |
| `index move <SRC> <DST>` | Move an indexed file (DST may be a directory or file path) |
| `index status <FILE>` | Check if a file needs re-indexing |

### Search

```
docsearch search -q QUERY [OPTIONS]
```

| Option | Description |
|---|---|
| `-q, --query TEXT` | Full-text search query |
| `--scope DIRECTORY` | Limit to subdirectory |
| `--type EXTENSION` | Filter by file extension |
| `--author NAME` | Filter by author |
| `--tag TAG` | Filter by tag (repeatable) |
| `--after DATE` | Modified after ISO date (YYYY-MM-DD) |
| `--before DATE` | Modified before ISO date (YYYY-MM-DD) |
| `--document-types TYPES` | Comma-separated document types (generic, paper, textbook, reference) |
| `--limit N` | Max results (default: 50) |
| `--offset N` | Pagination offset |
| `-f FORMAT` | Output: `text`, `json`, or `csv` (default: `text`) |

**Scope semantics:** `--scope` matches the given directory *and everything under it*, including documents stored directly in that directory. Matching is on path components, so `--scope docs` includes `docs/paper.pdf` and `docs/sub/paper.pdf`, but not `docs_extra/paper.pdf`. An empty scope (or `/`) covers the whole index. Chapter results honour the same scope, matched against the parent textbook's directory.

### Document Retrieval

| Command | Description |
|---|---|
| `get <DOC_ID>` | Retrieve extracted text (`-f text\|json`) |
| `bibtex <DOC_ID>` | Export BibTeX entry (papers only) |

### References and Document Operations

| Command | Description |
|---|---|
| `reference` | Register a metadata-only reference (`-t TITLE`, `-a AUTHOR`, `-s SUBJECT`, `-k KEYWORDS`, `-u URL`, `-p PATH`, `-T TYPE`, `-m KEY=VALUE`) |
| `document attach <DOC_ID> <FILE>` | Attach a local file to an existing reference entry |
| `document detach <DOC_ID>` | Detach the physical file from a document, converting to reference |

### Filesystem Browsing

| Command | Description |
|---|---|
| `ls [PATH]` | List indexed contents of a directory (`-f text\|json`) |

### Sidecar Metadata

| Command | Description |
|---|---|
| `meta show <FILE>` | Display metadata (from the index; falls back to the sidecar file when the path isn't indexed) |
| `meta set <FILE>` | Set a key on an indexed document (`-k KEY -v VALUE`) |
| `meta delete <FILE>` | Remove a key from an indexed document (`-k KEY`) |
| `meta init <FILE>` | Create empty sidecar file |

`set` and `delete` update both places a metadata key lives — the `documents.sidecar_metadata` column (what search, tag filters and every read path use) and the `.meta.json` file (what a re-scan reads back) — in one step, without re-extracting the document. Writing only one of them would leave an edit either invisible or lost on the next scan. Reference-only entries are supported even though they have no file on disk; keys you added to `.meta.json` by hand survive when you edit a different key.

**Value parsing (`-v`, and `-m KEY=VALUE` on every add/upload/reference command):** values are parsed as JSON when possible, so `-v 2018` stores the number `2018` and `-v '["ml","flow"]'` stores a list. Identifiers that look like numbers are lossy this way — `1706.03762` becomes the float `1706.0376`, losing the trailing zero — so quote them to keep the exact string:

```bash
docsearch meta set paper.pdf -k arxiv_id -v '"1706.03762"'
```

### Index Repair

| Command | Description |
|---|---|
| `repair check` | Report what needs repairing, changing nothing (`--check NAME`, `-v`) |
| `repair apply` | Repair it in place (`--check NAME`, `-v`) |

| Check | Fixes |
|---|---|
| `control-characters` | Strips C0 control characters from stored document and chapter text — PyMuPDF wraps some inline-math glyph runs in U+0000/U+0001 markers, and a single embedded NUL makes SQLite's `length()` report the text as truncated at that point |

Repairs cover only damage docsearch itself wrote into data it owns. Your own metadata keys are never treated as corruption and no check rewrites them. Applying a fix does not re-extract source files, so `content_hash`, `mtime` and `indexed_at` are left alone; running `apply` twice is harmless.

### Papers

| Command | Description |
|---|---|
| `papers add <FILE>` | Add research paper (`--doi`, `--skip-bib`, `-m KEY=VALUE`) |
| `papers upload <FILE>` | Upload and auto-index (`-n NAME`, `-D DIR`, `--doi`, `--skip-bib`, `-m KEY=VALUE`) |
| `papers reference` | Register metadata-only paper reference (`-t TITLE`, `-a AUTHOR`, `-y YEAR`, `-j JOURNAL`, `-b BOOKTITLE`, `-d DOI`, `-u URL`, `-k CITATION_KEY`, `-p PATH`, `-m KEY=VALUE`) |

**Note on DOI format:** When using `--doi`, provide a full DOI (e.g. `10.48550/arXiv.2506.13131` for arXiv papers). Bare arXiv IDs (e.g. `2506.13131`) may cause pdf2bib to hang indefinitely on network lookup with no timeout or error message. Use `--skip-bib` as a workaround if the DOI format is non-standard or the network call hangs.

### Textbooks

| Command | Description |
|---|---|
| `textbooks add <FILE>` | Add textbook (`-m KEY=VALUE`) |
| `textbooks upload <FILE>` | Upload and auto-index (`-n NAME`, `-D DIR`, `-m KEY=VALUE`) |
| `textbooks reference` | Register metadata-only textbook reference (`-t TITLE`, `-a AUTHOR`, `-y YEAR`, `--publisher`, `-e EDITION`, `-u URL`, `-D PATH`, `-m KEY=VALUE`) |
| `textbooks init <DIR>` | Initialize empty directory-type textbook (`-t TITLE`, `-m KEY=VALUE`) |
| `textbooks attach-chapter <DOC_ID> <FILE>` | Associate local chapter file with directory textbook (`-i INDEX`) |
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

`scope` behaves as it does in the CLI: the directory itself plus its subtree, matched on path components.

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
| `PATCH` | `/documents/{id}/meta` | Update sidecar key body: `{key, value}` → `{updated, key, metadata}`. Writes the DB column and `.meta.json` together without re-extracting; works for reference-only entries (404 if no such document) |
| `GET` | `/documents/{id}/bibtex` | Export BibTeX (papers only, 400 if not paper) |
| `POST` | `/documents/{id}/move` | Move document body: `{destination}` → `{id, old_path, new_path, filename}` |
| `POST` | `/documents/{id}/attach` | Attach file to reference-only entry (multipart, query: `directory`, `filename`) → converts source_type to "file, preserves existing metadata via sidecar |
| `POST` | `/documents/{id}/detach` | Detach file from document → converts source_type to "reference", deletes physical file, clears full_text and extracted_metadata, preserves sidecar |
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
| `POST` | `/documents/textbooks/upload` | Upload textbook (multipart, query: `extra_metadata`, `directory`, `filename`, `variant`, `chapter_breakpoints`) → `{id, path, filename}` |
| `POST` | `/documents/{id}/chapters/upload` | Upload chapter file to directory-type textbook (multipart, query: `filename`, `chapter_index`) → chapter metadata |

#### Chapter Breakpoints

The `chapter_breakpoints` query parameter (file-type textbooks only) lets you split a PDF into chapters at upload time without writing a sidecar file. Two formats are accepted:

**List** — N page boundaries imply N+1 chapters (`[0..bp₀], [bp₀..bp₁], …, [bp₋₁..end]`):
```
chapter_breakpoints=[5,10,15]
# → Chapter 1 (pp. 0–5), Chapter 2 (pp. 5–10), Chapter 3 (pp. 10–15), Chapter 4 (pp. 15–end)
```

**Dict** — Keys are chapter names, values are end pages (exclusive); `null` means "to end of book":
```
chapter_breakpoints={"Introduction":5,"Methods":10,"Results":null}
# → Introduction (pp. 0–5), Methods (pp. 5–10), Results (pp. 10–end)
```

Chapters are sorted by page order. The first chapter always starts at page 0.

#### Directory-Type Textbooks

Creating a directory-type textbook (`variant=directory`) requires the `filename` query parameter — it determines the directory name and is used as the default title in metadata.

## Architecture

```
docsearch/
├── config.py        — Central Config (database home, db path resolution)
├── core/            — Data models, SQLite repository, indexer, handlers
│   ├── models.py    — Document, Chapter, TextRow, SearchResult, SearchQuery
│   ├── repository.py — SQLite + FTS5 repository
│   ├── indexer.py   — Directory scanning, file add/remove, metadata edits
│   ├── sidecars.py  — Sidecar (.meta.json) location and IO
│   ├── repair.py    — Registry of integrity checks over stored data
│   └── handlers.py  — DocumentHandler pipeline (generic, paper, textbook, reference)
├── extractors/      — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/             — Click-based CLI commands
│   ├── utils.py     — CLI path resolution helper (CWD-aware, home-contained)
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
