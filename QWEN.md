# docsearch

Document metadata index and search engine for managing reference material (research papers, textbooks, etc.) as model context. Provides both a CLI and a REST API for indexing, searching, and querying files by content and metadata.

## Architecture

```
docsearch/
├── config.py        — Central Config class (database home, db path resolution)
├── core/            — Data models, SQLite repository, indexer, handlers
│   ├── models.py    — Document, Chapter, TextRow, SearchResult, SearchQuery dataclasses
│   ├── repository.py — SQLite + FTS5 repository
│   ├── indexer.py   — Directory scanning, file add/remove (delegates to handlers), metadata edits
│   ├── sidecars.py  — Sidecar location/IO: sidecar_path(), load_sidecar(), write_sidecar()
│   ├── repair.py    — Registry of integrity checks over data this program wrote
│   └── handlers.py  — DocumentHandler pipeline (generic, paper, textbook, reference)
├── extractors/      — Pluggable file-type extractors (PDF, DOCX, Markdown)
├── cli/             — Click-based CLI commands
│   ├── utils.py     — CLI path resolution + `-m`/`-v` value parsing helpers
│   └── commands/    — index, search, get, meta, bibtex, papers, textbooks, reference, document, ls, repair
└── server/          — FastAPI REST API
    ├── app.py       — App factory, lifespan, health endpoint
    ├── dependencies.py — Shared FastAPI dependencies (get_config)
    ├── schemas.py   — Pydantic request/response schemas
    └── routes/      — Route modules (documents, index, search, fs, papers, textbooks)
```

**Shared core layer:** Both the CLI and REST API use the same `Repository`, `Indexer`, and `DocumentHandler` classes from `core/`, ensuring consistency between interfaces.

## Configuration

### Database Home

The **database home** is an explicit root directory under which all data lives:
- All document paths are stored **relative** to the database home (portable across machines)
- Absolute paths are resolved dynamically as `config.home / doc.path` for filesystem operations
- File uploads are scoped within the database home
- **CLI path resolution:** User-supplied relative paths are resolved against the current working directory first, then validated as being within the database home. This allows natural usage from subdirectories (e.g., `cd home/proj_1 && docsearch papers add paper.pdf`). Absolute paths must also reside within the database home. The shared helper `resolve_user_path_to_home_relative()` in `cli/utils.py` implements this logic.

| | Default | Override |
|---|---|---|
| CLI | Current working directory (`.`) | `--home PATH` |
| REST API | Current working directory (`.`) | `DOCSEARCH_HOME` env var |

### Database Path

The SQLite database file defaults to `{home}/docsearch.db` but can be placed independently:

| | Default | Override |
|---|---|---|
| DB path | `{home}/docsearch.db` | `DOCSEARCH_DB_PATH` env var |

This decoupling is useful when the home directory is on a network mount with restrictive permissions — the documents live on the network drive while the database sits on local disk.

See `docsearch/config.py` — the `Config` class owns this logic with `resolve_path()` (user→absolute) and `relative_path()` (absolute→relative) helpers.

## Document Types

The system supports four document types, distinguished by the `document_type` column in the database:

| Type | Handler | Behavior |
|---|---|---|
| `generic` (default) | `GenericDocumentHandler` | Standard extract-and-index via file-type extractors |
| `paper` | `PaperDocumentHandler` | Research papers with pdf2bib bibliographic extraction, DOI embedding, title validation |
| `textbook` | `TextbookDocumentHandler` | Splits PDF into chapters via TOC detection, stores each chapter independently in `textbook_chapters` table |
| `reference` | `ReferenceDocumentHandler` | Metadata-only paper entries without associated files; auto-generates BibTeX |

The `Indexer.add_file()` and `Indexer.scan_directory()` methods accept a `document_type` parameter and delegate to the appropriate handler via `get_handler()`.

## Source Types

Documents have a `source_type` column indicating their origin:

| Value | Applies To | Description |
|---|---|---|
| `file` | All types | Document backed by a file on disk (implicit default when not set) |
| `directory` | Textbooks only | Directory-based textbook with one file per chapter |
| `reference` | Papers only | Metadata-only entry with no associated file (uses `{citation_key}.bib` as relative path) |

This generalizes the former `textbook_variant` column (renamed in-place via migration).

## Key Components

### Core (`docsearch/core/`)

- **`models.py`** — `Document` dataclass (indexed document with extracted + sidecar metadata, `document_type`, `source_type`), `Chapter` (textbook chapter with `textbook_id`, `chapter_index`, `title`, `chapter_type`, `start_page`, `end_page`, `page_count`, `file_path`, `metadata`, `full_text`; `combined_metadata()` inherits from parent `Document`), `TextRow` (frozen: a stored extracted-text payload addressed by `kind` (`document`/`chapter`) + `id`, with a human-readable `label`; lets checks treat both tables uniformly), `SearchResult` (document + FTS score + optional `chapter` field + snippet), `SearchQuery` (search parameters: query string, scope, file type, author, tags, date range, `document_types` filter, pagination). `from_row()` supports `tuple`, `dict`, and `sqlite3.Row` inputs (uses `row.keys()` membership checks for mapping types).
- **`repository.py`** — SQLite-backed store with WAL journal mode; **performs no file I/O by design** (the Indexer owns the filesystem side). Uses FTS5 for full-text search over `filename`, `directory`, and `full_text`. Dynamic SQL query builder supporting filters on scope, extension, author (via `json_extract`), tags, date range, and `document_types`. Scope uses the module-level `_scope_clause()` helper — `col = ? OR col LIKE ?/'%'`, rstripping trailing slashes and degrading to `1=1` for an empty scope — shared by `search()` and `_resolve_textbook_ids()` so document and chapter results honour it identically. Methods: `upsert`, `search`, `get`, `get_by_id`, `remove`, `count`, `all_paths`, `exists`, `list_directory` (filesystem-style directory listing that infers subdirectories from nested document paths), `rename` (update path/filename/directory in-place), `update_document` (selective field update by id), `update_sidecar_metadata` (read-modify-write of the sidecar column inside one transaction, tolerating corrupt JSON; DB half only — writing the file is the Indexer's job), `iter_texts` (streams `TextRow`s from both tables via `fetchmany`). Chapter methods: `upsert_chapter`, `get_chapters`, `get_chapter`, `delete_chapters`, `get_chapter_by_file_path`, `delete_chapter_by_id`, `search_textbook_chapters` (two-phase: resolve textbook IDs from metadata filters → FTS search within those chapters). Migration logic handles adding new columns and renaming existing ones (`_migrate_existing_columns`).
- **`sidecars.py`** — Single source of truth for sidecar location and IO, shared by handlers, indexer and CLI. `SIDECAR_SUFFIX` (`.meta.json`, also used by `scan_directory` to skip sidecar files), `sidecar_path(abs_path, source_type=None)` — takes an already-resolved absolute path; directory-type textbooks use `<dir>/<dirname>.meta.json`, everything else `<path>.meta.json` — plus fault-tolerant `load_sidecar()` (`{}` on missing/corrupt/non-object) and `write_sidecar()` (returns False and logs on IOError).
- **`repair.py`** — Registry of integrity checks over data this program wrote itself; user-authored metadata is explicitly out of scope. A `RepairCheck` has `name`, `description`, `scan(repo)` (read-only) and `apply(repo, findings)`; `TextTransformCheck` subclasses only need a pure `transform()` returning `None` to leave a value alone, which makes repairs idempotent. Registered in `_CHECKS`; `run(repo, names=..., apply=...)` drives them and `get_check()` raises listing valid names on a typo. First check: `control-characters`.
- **`indexer.py`** — Orchestrates indexing (filesystem + DB). Calls `load_extractors()` from the extractors package to build the extension→extractor map. Delegates document processing to `handlers.get_handler()`. Computes SHA-256 content hashes for change detection, loads `.meta.json` sidecar files. `scan_directory()` performs a full sync: detects new/changed/deleted files and updates the index accordingly. Short-circuits for `document_type="reference"` (no file required). `move_file()` moves physical file and DB entry (skips filesystem move for references). `attach_file()` attaches a physical file to an existing reference-only entry: renames DB path, writes preserved metadata to sidecar, re-indexes with `skip_bib=True`. Metadata edits go through `set_metadata_key()` / `delete_metadata_key()`, which merge the DB column with the on-disk sidecar (`_edit_metadata`), write both without re-extracting, and treat a failed file write as a logged warning rather than an error; `metadata_sidecar_path(doc)` exposes the location.
- **`handlers.py`** — Pipeline-based document processing framework. Base `DocumentHandler` class with `pre_process()`, `extract_metadata()`, `extract_text()`, `post_process()` hooks. All handlers set `doc.id` from the `upsert` return value so callers get an id without re-querying. Subclasses:
  - `GenericDocumentHandler` — default behavior, identical to legacy indexer
  - `PaperDocumentHandler` — embeds DOI via `pdf2doi`, runs pdf2bib for bibliographic metadata, validates title match between PDF metadata and extracted citation (title mismatch guard), moves pdf2bib author list to `authors_bib` key, stores raw bibtex string in sidecar. Falls back to `_generate_bibtex_from_metadata()` when `skip_bib=True`
  - `TextbookDocumentHandler` — dispatches on file vs directory. File-type: extracts PDF metadata, detects chapters via TOC/sidecar, inserts page-range chapters. Directory-type: enumerates first-level files as chapters, loads/saves `<dirname>.meta.json` sidecar inside directory, alphabetical default ordering overridable via sidecar `chapters` key
  - `ReferenceDocumentHandler` — creates metadata-only paper entries with `source_type='reference'`. Uses `{citation_key}.bib` as relative path for uniqueness. Populates `full_text` from title/author/journal/booktitle/abstract for FTS searchability. Auto-generates BibTeX when not provided
  - Helper functions: `_normalize_title()`, `_titles_match()`, `_format_author_dict()`, `_format_authors_bib()`, `_generate_bibtex_from_metadata()`

### Extractors (`docsearch/extractors/`)

All extend `BaseExtractor` (abstract: `supported_extensions`, `extract_metadata()`, `extract_text()`). Fault-tolerant — return empty results on failure rather than raising.

`BaseExtractor.extract()` passes `extract_text()` output through `sanitize_text()` (exported from `extractors/__init__.py`), which strips C0 control characters except `\t`, `\n` and `\r`. PyMuPDF emits U+0000/U+0001 markers around some glyph runs (typically inline math); an embedded NUL makes SQLite's `length()` report the value as truncated at that point. Code paths that bypass extractors — `handlers._extract_pages()` for range-type textbook chapters — sanitize explicitly.

The extractors package owns knowledge of available extractors via `load_extractors()` in `__init__.py`, which returns an `extension → BaseExtractor` dict. The `Indexer` calls this function at init — adding a new extractor only requires editing `extractors/__init__.py`.

| Extractor | Extensions | Metadata Source |
|---|---|---|
| `PdfExtractor` | `pdf` | PyMuPDF (title, author, subject, creator, producer, dates, page count) |
| `DocxExtractor` | `docx` | python-docx custom properties (title, author, subject, keywords, comments, dates) |
| `MarkdownExtractor` | `md`, `markdown`, `txt` | PyYAML frontmatter (everything between leading `---` delimiters) |

### Sidecar Metadata

User-editable metadata lives in `<filepath>.meta.json` alongside source documents. The indexer reads these and stores them separately from extractor-derived metadata. The `combined_metadata` property merges both (sidecar overrides extracted). This allows manual tagging/annotation without modifying source files.

**A key lives in two places and they must agree:** the `documents.sidecar_metadata` column (what search, tag filters and every read path use) and the `.meta.json` file (reloaded from disk by `DocumentHandler._load_sidecar()` on every re-index). Writing only one makes an edit either invisible or lost on the next scan — so edits go through `Indexer.set_metadata_key()` / `delete_metadata_key()`, which write both in one step without re-extracting. They merge the current column with the current file first, so hand-edited keys survive. Both the CLI (`meta set`/`meta delete`) and REST (`PATCH /documents/{id}/meta`) route through this; neither requires a file on disk, so reference-only entries work too.

For papers, the sidecar also stores raw bibtex and parsed bibliographic metadata from pdf2bib. For references, the sidecar contains all user-supplied metadata (the entire entry is metadata-only) — it is load-bearing there, holding keys with no DB column that a re-index reads back.

**Value parsing (`-m KEY=VALUE`, `meta set -v`):** handled by `parse_meta_value()`/`parse_meta_pairs()` in `cli/utils.py` (the single implementation shared by the `meta`, `papers`, `textbooks` and `reference` commands). JSON is tried first, so numbers/lists/objects work; anything unparseable is kept as a raw string. Quoting is therefore the convention for forcing a string — `-m arxiv_id='"1706.03762"'` — and it cannot be removed: `repr(1706.03762) == '1706.03762'`, so no round-trip test can distinguish an identifier from a float, and unquoted `1710.04820` silently loses its trailing zero.

### Database Schema

SQLite database with:
- **`documents`** table: path (unique key), filename, directory, extension, size, mtime, content_hash (SHA-256), extracted_metadata (JSON), sidecar_metadata (JSON), full_text, indexed_at, document_type (TEXT, default `'generic'`), source_type (TEXT, nullable)
- **`documents_fts`**: FTS5 virtual table on (filename, directory, full_text) with unicode61 tokenizer, auto-synced via triggers
- **`textbook_chapters`** table: id, textbook_id (FK→documents), chapter_index, title, chapter_type (`'range'` or `'file'`), start_page (nullable), end_page (nullable), page_count (nullable), file_path (nullable, relative path for file-type chapters), metadata (JSON), full_text
- **`textbook_chapters_fts`**: FTS5 virtual table on (title, full_text) with unicode61 tokenizer
- Indexes on `directory`, `extension`, `textbook_id`, and unique (textbook_id, chapter_index)
- Database file at `{home}/docsearch.db`

Migrations in `migrations/`:
- `001_initial.sql` — original schema (without `document_type`)
- `002_add_document_type.sql` — added `document_type` column

Migration SQL files are reference-only; the schema is embedded directly in `repository.py`'s `_SCHEMA_SQL`. Runtime migrations handle backward-compatible column additions and renames.

## CLI

Entry point: `docsearch` (maps to `docsearch.cli.main:cli`)

```
docsearch [--home PATH] COMMAND

Commands:
  info [DOC_ID]           Show database location and index statistics; with DOC_ID, show full document metadata
  index scan <DIR>        Scan directory tree and sync index (-T/--document-type TYPE, --no-recursive)
  index add <FILE>        Add a single generic file to the index
  index remove <FILE>     Remove a file from the index
  index move <SRC> <DST>  Move an indexed file to a new location (DST may be a directory)
  index status <FILE>     Check if a file needs re-indexing
  search                  Search indexed documents (-q, --scope, --type, --author, --tag, --after/--before, --document-types, --limit, --offset, -f)
  get <DOC_ID>            Retrieve extracted text content (-f text/json)
  bibtex <DOC_ID>         Export BibTeX for a paper
  reference               Register a metadata-only generic reference (-t TITLE, -a AUTHOR, -s SUBJECT, -k KEYWORDS, -u URL, -p PATH, -T TYPE, -m KEY=VALUE)
  document attach <ID> <FILE>  Attach a local file to an existing reference entry
  document detach <ID>    Detach the physical file from a document, converting to reference
  ls [PATH]               List indexed contents of a directory (-f text/json)
  meta show <FILE>        Display metadata (index first, sidecar file if not indexed)
  meta set <FILE>         Set a key on an indexed document (-k KEY -v VALUE); updates column + sidecar, no re-extraction
  meta delete <FILE>      Remove a key from an indexed document (-k KEY)
  meta init <FILE>        Create empty sidecar file
  repair check            Report index corruption without changing anything (--check NAME, -v)
  repair apply            Repair it in place (--check NAME, -v)
  papers add <FILE>       Add a research paper (--doi, --skip-bib, -m KEY=VALUE)
  papers upload <FILE>    Upload a paper (--doi, --skip-bib, -n NAME, -D DIRECTORY)
  papers reference        Register metadata-only paper reference (-t TITLE, -a AUTHOR, -y YEAR, -j JOURNAL, -b BOOKTITLE, -d DOI, -u URL, -k CITATION_KEY, -p PATH, -m KEY=VALUE)
  textbooks add <FILE>    Add a textbook (-m KEY=VALUE)
  textbooks upload <FILE> Upload a textbook (-n NAME, -D DIRECTORY)
  textbooks reference     Register metadata-only textbook reference (-t TITLE, -a AUTHOR, -y YEAR, --publisher, -e EDITION, -u URL, -D PATH, -m KEY=VALUE)
  textbooks init <DIR>    Initialize empty directory-type textbook (-t TITLE, -m KEY=VALUE)
  textbooks attach-chapter <ID> <FILE>  Associate local chapter file with directory textbook (-i INDEX)
  textbooks chapters <FILE>   List indexed chapters
  textbooks chapter <FILE>    Print chapter text (-i CHAPTER_INDEX)
```

Search supports: `-q QUERY`, `--scope DIR`, `--type EXT`, `--author NAME`, `--tag TAG` (repeatable), `--after/--before DATE`, `--document-types TYPES` (comma-separated), `--limit N`, `--offset N`, `-f FORMAT` (text/json/csv). All output formats include the document `id`.

`--scope` matches the directory itself **and** its subtree, on path components: `--scope docs` includes `docs/a.pdf` and `docs/sub/a.pdf` but not `docs_extra/a.pdf`; empty or `/` means everything. Chapter results are scoped by their parent textbook's directory.

## REST API

Entry point: `docsearch-server` (maps to `docsearch.server.app:main`, starts uvicorn on `0.0.0.0:8000`)

All routes share a single `get_config()` dependency from `server/dependencies.py` that resolves the `Config` once at startup.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check (returns home and db path) |
| GET | `/api/search` | Search (query params: q, scope, file_type, author, tags, after, before, offset, limit) |
| GET | `/api/fs` | List directory contents (query param: `path` relative to db home) → `{path, entries, directories}` |
| POST | `/api/index/scan` | Scan directory body: `{dirpath, recursive, document_type, extra_metadata}` |
| POST | `/api/index/add` | Add file body: `{filepath, document_type, extra_metadata}` |
| POST | `/api/index/remove` | Remove file body: `{filepath}` |
| POST | `/api/index/upload` | Upload + auto-index (multipart, `directory`/`filename` query params) |
| GET | `/api/documents/{id}` | Get document metadata by ID |
| GET | `/api/documents/{id}/content` | Get extracted text content (`ContentResponse`) |
| GET | `/api/documents/{id}/file` | Download original file from disk (`FileResponse`; 404 for references) |
| GET | `/api/documents/{id}/meta` | Get sidecar metadata |
| PATCH | `/api/documents/{id}/meta` | Update sidecar key body: `{key, value}` → `{updated, key, metadata}`; writes column + `.meta.json` together without re-extracting (works for references), 404 if no such document |
| GET | `/api/documents/{id}/bibtex` | Export BibTeX (papers only, 400 if not paper type) |
| POST | `/api/documents/{id}/move` | Move document body: `{destination}` |
| POST | `/api/documents/{id}/attach` | Attach file to reference (multipart, query: `directory`, `filename`) → converts source_type to "file |
| POST | `/api/documents/{id}/detach` | Detach file from document → converts source_type to "reference", clears full_text/extracted_metadata |
| GET | `/api/documents/{id}/chapters` | List textbook chapters (textbooks only) |
| GET | `/api/documents/{id}/chapters/{index}` | Get chapter by index (textbooks only) |
| POST | `/api/documents/papers/add` | Add paper body: `{filepath, doi?, skip_bib?, extra_metadata?}` |
| POST | `/api/documents/papers/upload` | Upload paper (multipart, query: `doi`, `skip_bib`, `extra_metadata`, `directory`, `filename`) |
| POST | `/api/documents/papers/reference` | Register metadata-only reference body: `{title, author?, year?, journal?, booktitle?, doi?, url?, bibtex?, citation_key?, extra_metadata?}` |
| POST | `/api/documents/textbooks/add` | Add textbook body: `{filepath, extra_metadata?}` |
| POST | `/api/documents/textbooks/upload` | Upload textbook (multipart, query: `extra_metadata`, `directory`, `filename`, `variant`, `chapter_breakpoints`) |
| POST | `/api/documents/{id}/chapters/upload` | Upload chapter to directory-type textbook (multipart, query: `filename`, `chapter_index`) |

**Chapter breakpoints** (`chapter_breakpoints`, file-type only): JSON list `[5,10,15]` (N boundaries → N+1 chapters) or dict `{"Intro":5,"Methods":10,"Results":null}` (keys are titles, values are end pages; `null` means "to end of book). First chapter always starts at page 0.

**Directory-type textbooks** (`variant=directory`) require the `filename` query parameter — it determines the directory name and is used as the default title in metadata.

Pydantic request/response schemas in `server/schemas.py`. `DocumentResponse` includes `id`, `document_type`, and `source_type`. Upload endpoints save files relative to the database home with strict path-traversal protection.

## Tests

Located in `tests/`, run with `pytest`.

| File | Coverage |
|---|---|
| `test_repository.py` | `Document` model (`combined_metadata`, `from_row`), `Repository` (upsert, remove, count, all_paths, search with FTS/scope/author/extension/tags/limit filters, get_by_id, exists), scope clause semantics (documents directly in the scope dir, scope without a query, trailing-slash normalisation, root matching everything, `/doc` not matching `/docs`, chapter search honouring scope), `list_directory` (empty dir, files only, inferred subdirs, deeply nested, root listing, mixed files/subdirs, directory-type textbook as directory entry, deduplication, reference documents, sorted ordering) |
| `test_extractors.py` | `PdfExtractor` (metadata extraction, text extraction, multi-page, fault tolerance) |
| `test_handlers.py` | BibTeX helpers (`_normalize_title`, `_titles_match`, `_format_author_dict`, `_format_authors_bib`, `_generate_bibtex_from_metadata`), `PaperDocumentHandler` integration (skip_bib, DOI embedding, title mismatch logic, authors_bib handling) |
| `test_server.py` | REST API content/file endpoints (`/content`, `/file`), upload (basic, subdirectory, custom name, path traversal rejection, nonexistent dir, unsupported type), BibTeX endpoint, paper endpoints (add/upload with DOI), textbook endpoints (add/upload), chapter endpoints (list, get, search), directory textbook endpoints (empty dir creation, chapter upload, auto-indexing, overwrite, path traversal), reference endpoints (basic, DOI, bibtex generation, custom bibtex, title validation, citation key, extra metadata, searchable content, file download 404, search integration, duplicate upsert), filesystem browsing (`/api/fs`: root listing, subdirectory files, mixed files/dirs, directory-type textbook as directory entry, empty dir, path traversal rejection, path field, deeply nested immediate children), attach/detach endpoints (attach converts reference to file, preserves metadata via sidecar, populates full_text, rejects non-reference/directory sources, subdirectory/custom filename, path traversal rejection; detach converts file to reference, deletes physical file, clears full_text, preserves sidecar metadata, rejects reference/directory sources; round-trip attach→detach cycle), PATCH `/meta` endpoint (column and `.meta.json` kept in agreement — verified via a tag filter hit as well as the file on disk, sidecar location, merge-not-replace, survives re-index, no re-extraction, hand-written keys preserved, structured values, overwrite, reference entries incl. preserved bibliographic metadata, 404 unknown id, 422 missing key) |
| `test_cli_path_resolution.py` | CLI path resolution helper unit tests (CWD-relative paths, absolute paths, outside-home rejection, existence/type checks), CLI integration tests for `index add/scan/remove/move` from subdirectories, `index move` with directory destinations (move into dir keeping name, trailing slash, new subdir creation, home containment) |
| `test_papers_cli_path_resolution.py` | CLI integration tests for `papers add` and `textbooks add` from subdirectories (bare filename, absolute path, cwd independence) |
| `test_sanitization.py` | `sanitize_text()` (C0 controls stripped, tab/newline/CR preserved), applied via `BaseExtractor.extract()`, and `handlers._extract_pages` for range-type chapters that bypass extractors |
| `test_metadata_updates.py` | `Indexer.set/delete_metadata_key` (both stores updated, existing keys preserved, delete absent key is not an error, unknown id False, survives re-index, hand-edited sidecar merged, no re-extraction on edit, reference entries get `<citation_key>.bib.meta.json`, directory-textbook sidecar lives inside the dir), `Repository.update_sidecar_metadata` (patch merges, remove_keys, unknown id False, corrupt column tolerated), CLI `meta set/delete/show` incl. unindexed-path error and file fallback, `-v`/`-m` JSON-vs-quoted-string parsing |
| `test_repair.py` | `control-characters` check (detects NUL-laden text, scan is read-only, apply strips and preserves meaningful whitespace, SQL `length()` matches Python length after repair, FTS still retrieves text past the NUL, chapter text repaired with parent-labelled findings, idempotent), `content_hash`/`mtime`/`indexed_at` untouched, registry (`all_checks`, `get_check` error listing names, name filter), CLI `repair check`/`apply` output and `-v` collapse |
| `conftest.py` | Shared fixtures: `sample_pdf_with_metadata`, `sample_pdf_no_metadata`, `sample_pdf_multipage` (generated on-the-fly via PyMuPDF) |
| `fixtures/documents.py` | Duplicate of conftest.py fixtures (not currently imported) |

### Test Coverage Gaps

- No tests for `DocxExtractor` or `MarkdownExtractor`
- No tests for CLI search/get/bibtex/ls/info/reference commands (Click testing)
- No tests for `Config` class
- No tests for `Indexer.scan_directory()` full sync logic

## Dependencies

- **Runtime:** click, fastapi, uvicorn, python-multipart, pymupdf, python-docx, pyyaml, pydantic, pdf2bib
- **Dev:** pytest, pytest-asyncio, httpx, mypy

## Conventions

- All extractors are fault-tolerant (catch exceptions, return empty results)
- Content-hash-based change detection avoids unnecessary re-indexing
- Extractor registry is owned by `extractors/__init__.py` (`load_extractors()`)
- Handler registry is owned by `core/handlers.py` (`get_handler()`)
- Database home is explicit; all paths resolve relative to it (`Config` class)
- Single shared `get_config()` dependency across all server routes
- **Type checking is not enforced.** Annotations are kept because they document intent and drive editor support, but `mypy` is not a gate: `[tool.mypy] strict = true` stays in `pyproject.toml` for ad-hoc use only, and the package has ~190 pre-existing errors under it. Don't chase those when changing code — verify with pytest.
- Migration SQL in `migrations/` (schema also embedded in `repository.py`)
- Test PDFs are generated programmatically (no binary fixtures committed to repo)
- References use `{citation_key}.bib` as relative path for uniqueness in the documents table

## Known Limitations

- CLI path resolution (`cli/utils.py`) resolves user-supplied relative paths against CWD first, then validates containment within database home — the API does not use this helper (no CWD concept)
- `index move` destination may be an existing directory (file keeps its name) or a new file path; both cwd-relative and absolute destinations are supported
- **Snippet support:** `SearchResult` has a `snippet` field but it is never populated (always empty string)
- **Migration runner:** Migration files exist but are reference-only; no automated migration execution
- **Sanitization is forward-only:** `sanitize_text()` applies to new extractions; text already stored by older versions keeps its control characters until `docsearch repair apply` rewrites it
- **Repair scope is deliberate:** checks only cover corruption this program introduced in data it owns. User-authored metadata (a mistyped tag, an `arxiv_id` that parsed as a float) is not corruption and no check will rewrite it — quoting at entry time is the fix for that
