# docsearch

Document metadata index and search engine for managing reference material (research papers, textbooks, etc.) as model context. Extracts text from PDFs, DOCX files, and Markdown; stores metadata in SQLite with FTS5 full-text search; provides CLI and REST API.

## Features

- **Multi-format extraction** — PDF (PyMuPDF), DOCX (python-docx), Markdown/Text (PyYAML frontmatter)
- **Full-text search** — FTS5 with filters on scope, file type, author, tags, date range, document type
- **Document types** — generic, paper (with BibTeX via pdf2bib), textbook (chapter-level indexing), reference (metadata-only)
- **Sidecar metadata** — Editable `<file>.meta.json` for tagging without modifying source files
- **Document sections** — Define named line ranges in sidecar metadata; retrieve partial content to reduce context bloat
- **Two interfaces** — Click CLI for local workflows, FastAPI REST API for remote access

## Installation

```bash
pip install -e .
```

For development: `pip install -e ".[dev]"`

## Quick Start

### CLI

```bash
# Scan a directory of papers
docsearch index scan ./papers -T paper

# Search indexed documents
docsearch search -q "transformer attention"

# Retrieve full content or specific sections
docsearch get 42
docsearch get 42 --sections 0,2

# Tag a document via sidecar metadata
docsearch meta set ./papers/survey.pdf -k tag -v nlp
```

### REST API

```bash
# Start server (defaults to 0.0.0.0:8000)
docsearch-server
```

Set `DOCSEARCH_HOME` to control the database home directory (default: cwd).
Set `DOCSEARCH_DB_PATH` to place the SQLite database independently (default: `{home}/docsearch.db`).

Interactive API docs at `http://localhost:8000/docs`.

## Configuration

| | Default | Override |
|---|---|---|
| CLI home | Current working directory | `--home PATH` |
| API home | Current working directory | `DOCSEARCH_HOME` env var |
| Database path | `{home}/docsearch.db` | `DOCSEARCH_DB_PATH` env var |

All document paths are stored **relative** to the database home, making the index portable. The database file can live independently (useful when home is a network mount).

### CLI Path Resolution

CLI commands resolve relative paths against cwd first, then validate containment within database home. This allows natural usage from subdirectories:

```bash
cd ~/docs/home/proj_1
docsearch papers add paper.pdf    # indexes as proj_1/paper.pdf
```

## Document Types

| Type | Description |
|---|---|
| `generic` | Standard extract-and-index (default) |
| `paper` | Research papers with pdf2bib, DOI embedding, title validation, BibTeX export |
| `textbook` | PDFs split into chapters via TOC detection, each indexed independently |
| `reference` | Metadata-only entries without associated files (BibTeX auto-generated) |

## Source Types

| Source Type | Applies To | Description |
|---|---|---|
| `file` | All types | Backed by a file on disk (default) |
| `directory` | Textbooks only | Directory-based textbook, one file per chapter |
| `reference` | Papers only | Metadata-only entry with no associated file |

## CLI Reference

### Index Management

| Command | Description |
|---|---|
| `index scan <DIR>` | Scan directory tree and sync index (`-T TYPE`, `--no-recursive`) |
| `index add <FILE>` | Add single file to index |
| `index remove <FILE>` | Remove file from index |
| `index move <SRC> <DST>` | Move indexed file (DST may be directory) |

### Search

```
docsearch search -q QUERY [OPTIONS]
```

| Option | Description |
|---|---|
| `-q QUERY` | Full-text query (plain text by default, see below) |
| `--scope DIR` | Limit to subdirectory and subtree |
| `--type EXT` | Filter by file extension |
| `--author NAME` | Filter by author (partial match across merged metadata) |
| `--tag TAG` | Filter by tag (repeatable) |
| `--after/--before DATE` | Date range filter (ISO format) |
| `--document-types TYPES` | Comma-separated types |
| `--raw-fts` | Pass query to FTS5 verbatim |
| `--limit/--offset N` | Pagination |
| `-f FORMAT` | Output: `text`, `json`, or `csv` |

**Query semantics:** `-q` is plain text by default — FTS5 operator characters (`-`, `(`, `*`, etc.) are searched literally. Trailing `*` still works as prefix. Use `--raw-fts` for FTS5 syntax (`NEAR()`, `OR`, `^boost`).

**Author matching:** Reads merged metadata (sidecar over extracted) across `author`/`authors`/`authors_bib`. Matches by containment: `Schulman` finds "John Schulman" and either name in `"A and B"`.

### Document Retrieval

| Command | Description |
|---|---|
| `get <DOC_ID>` | Retrieve extracted text (`-f text\|json`, `--sections IDX`, `--lines RANGES`) |
| `bibtex <DOC_ID>` | Export BibTeX (papers only) |

### Document Sections

Sections let you define named line ranges in sidecar metadata, then retrieve partial content:

```bash
# Define sections
docsearch meta set-section paper.pdf --name "Abstract" --start 0 --end 49
docsearch meta set-section paper.pdf --name "Methods" --start 100 --end 299

# List and retrieve
docsearch meta list-sections paper.pdf
docsearch get 42 --sections 0,2          # by section index
docsearch get 42 --lines "0-99,200-299"  # by line ranges
```

### Metadata

| Command | Description |
|---|---|
| `meta show <FILE>` | Display metadata (`-k KEY` for single field) |
| `meta set <FILE>` | Set key on indexed document (`-k KEY -v VALUE`) |
| `meta delete <FILE>` | Remove key from indexed document |
| `meta init <FILE>` | Create empty sidecar file |

`set` and `delete` update both the DB column and `.meta.json` without re-extracting. Reference entries are supported.

**Value parsing:** `-v` and `-m KEY=VALUE` parse JSON when possible (`-v 2018` stores integer). Quote identifiers that look like numbers: `-v '"1706.03762"'`.

### Papers

| Command | Description |
|---|---|
| `papers add <FILE>` | Add paper (`--doi`, `--skip-bib`, `-m KEY=VALUE`) |
| `papers upload <FILE>` | Upload and auto-index (`-n NAME`, `-D DIR`) |
| `papers reference` | Register metadata-only reference (`-t TITLE`, `-a AUTHOR`, `-y YEAR`, `-j JOURNAL`, `-d DOI`, `-k CITATION_KEY`) |

Use full DOIs (e.g. `10.48550/arXiv.2506.13131`) — bare arXiv IDs may hang pdf2bib. Title validation corroborates retrieved metadata against the file before storing.

### Textbooks

| Command | Description |
|---|---|
| `textbooks add <FILE>` | Add textbook (`-n NAME`, `-D DIR`, `-m KEY=VALUE`) |
| `textbooks reference` | Register metadata-only textbook reference |
| `textbooks init <DIR>` | Initialize empty directory-type textbook |
| `textbooks set-chapters <DOC_ID>` | Redefine chapter breakpoints for file-type textbook (`-b BREAKPOINTS`) |
| `textbooks attach-chapter <ID> <FILE>` | Associate chapter file with directory textbook (`-i INDEX`) |
| `textbooks detach-chapter <ID> <INDEX>` | Remove chapter from directory textbook |
| `textbooks chapters <FILE>` | List indexed chapters |
| `textbooks chapter <FILE>` | Print chapter text (`-i INDEX`) |

**Chapter breakpoints:** List `[5,10]` for page boundaries (N boundaries → N+1 auto-named chapters), or dict `{"Intro":5,"Methods":10}` for named chapters. First chapter always starts at page 0.

### References and File Operations

| Command | Description |
|---|---|
| `reference` | Register metadata-only generic reference |
| `document attach <ID> <FILE>` | Attach file to reference entry |
| `document detach <ID>` | Detach file, converting to reference |

### Index Repair

| Command | Description |
|---|---|
| `repair check` | Report corruption without changing anything (`--check NAME`, `-v`) |
| `repair apply` | Repair in place (`--check NAME`, `-v`) |

Check `control-characters` strips C0 controls from stored text (PyMuPDF emits NUL markers around inline math).

## REST API Reference

All routes prefixed with `/api`.

### Health & Search

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/search` | Full-text search (params: `q`, `scope`, `file_type`, `author`, `tags`, `after`, `before`, `document_types`, `raw_fts`) |

### Index

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/index/scan` | Scan directory (`{dirpath, recursive?, document_type?, extra_metadata?}`) |
| `POST` | `/index/add` | Add file (`{filepath, document_type?, extra_metadata?}`) |
| `POST` | `/index/remove` | Remove file (`{filepath}`) |
| `POST` | `/index/upload` | Upload + auto-index (multipart, query: `directory`, `filename`) |

### Documents

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/documents/{id}` | Get metadata |
| `GET` | `/documents/{id}/content` | Get text (`?lines=RANGES` for line slicing) |
| `GET` | `/documents/{id}/file` | Download original file (404 for references) |
| `GET` | `/documents/{id}/meta` | Get sidecar metadata |
| `PATCH` | `/documents/{id}/meta` | Update sidecar key (`{key, value}`) |
| `GET` | `/documents/{id}/bibtex` | Export BibTeX (papers only) |
| `POST` | `/documents/{id}/move` | Move document (`{destination}`) |
| `POST` | `/documents/{id}/attach` | Attach file to reference (multipart) |
| `POST` | `/documents/{id}/detach` | Detach file, converting to reference |

### Document Sections

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/documents/{id}/sections` | List defined sections |
| `POST` | `/documents/{id}/sections` | Add section (`{name, start, end?}`) |
| `GET` | `/documents/{id}/sections/{index}` | Get section content |
| `DELETE` | `/documents/{id}/sections/{index}` | Delete section (re-indexes remaining) |

### Textbooks

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents/textbooks/add` | Add textbook (`{filepath, extra_metadata?}`) |
| `POST` | `/documents/textbooks/upload` | Upload textbook (multipart, query: `variant`, `chapter_breakpoints`) |
| `GET` | `/documents/{id}/chapters` | List chapters |
| `GET` | `/documents/{id}/chapters/{index}` | Get chapter by index |
| `PUT` | `/documents/{id}/chapters` | Redefine breakpoints (`{breakpoints}`) |
| `DELETE` | `/documents/{id}/chapters/{index}` | Delete directory-type chapter |
| `POST` | `/documents/{id}/chapters/upload` | Upload chapter file (directory textbooks) |

### Papers

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents/papers/add` | Add paper (`{filepath, doi?, skip_bib?}`) |
| `POST` | `/documents/papers/upload` | Upload paper (multipart) |
| `POST` | `/documents/papers/reference` | Register reference (`{title, author?, year?, ...}`) |

### Filesystem

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/fs` | List directory contents (query: `path`) → `{entries, directories}` |

## Architecture

```
docsearch/
├── config.py        — Config (database home, db path resolution)
├── core/            — Data models, SQLite repo, indexer, handlers
│   ├── models.py    — Document, Chapter, TextRow, SearchResult, SearchQuery
│   ├── repository.py — SQLite + FTS5 repository
│   ├── indexer.py   — Directory scanning, file add/remove, metadata edits
│   ├── slicing.py   — Runtime line slicing for document sections
│   ├── sidecars.py  — Sidecar (.meta.json) location and IO
│   ├── repair.py    — Integrity checks over stored data
│   └── handlers.py  — DocumentHandler pipeline (generic, paper, textbook, reference)
├── extractors/      — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/             — Click-based CLI commands
└── server/          — FastAPI REST API
    ├── schemas.py   — Pydantic request/response schemas
    └── routes/      — Route modules
```

Both CLI and API share the same `Repository`, `Indexer`, and `DocumentHandler` classes from `core/`.

## Testing

```bash
pytest
```

## Dependencies

- **Runtime:** click, fastapi, uvicorn, python-multipart, pymupdf, python-docx, pyyaml, pydantic, pdf2bib
- **Dev:** pytest, pytest-asyncio, httpx, mypy
