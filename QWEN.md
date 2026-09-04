# docsearch

Document metadata index and search engine for managing reference material as model context. CLI + REST API for indexing, searching, and querying files by content and metadata.

## Architecture

```
docsearch/
├── config.py        — Config class (database home, db path resolution)
├── core/            — Data models, SQLite repo, indexer, handlers
│   ├── models.py    — Document, Chapter, Supplement, TextRow, SearchResult, SearchQuery dataclasses
│   ├── repository.py — SQLite + FTS5 repository (no file I/O by design)
│   ├── indexer.py   — Directory scanning, file add/remove, metadata edits, convert_to_directory()
│   ├── slicing.py   — Runtime line slicing for document/chapter/supplement sections
│   ├── sidecars.py  — Sidecar location/IO: sidecar_path(), load/write_sidecar()
│   ├── repair.py    — Registry of integrity checks over program-written data
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

Both CLI and REST API share the same `Repository`, `Indexer`, and `DocumentHandler` classes from `core/`.

## Configuration

### Database Home

The **database home** is an explicit root directory under which all data lives:
- All document paths stored **relative** to home (portable across machines)
- Absolute paths resolved as `config.home / doc.path` for filesystem operations
- **CLI path resolution:** relative paths resolved against cwd first, then validated within home. Helper `resolve_user_path_to_home_relative()` in `cli/utils.py`. The `meta` commands use `document_path_candidates()` instead (yields both cwd-relative and home-relative interpretations).

| | Default | Override |
|---|---|---|
| CLI | Current working directory (`.`) | `--home PATH` |
| REST API | Current working directory (`.`) | `DOCSEARCH_HOME` env var |

### Database Path

SQLite database defaults to `{home}/docsearch.db` but can be placed independently via `DOCSEARCH_DB_PATH` env var. Useful when home is a network mount with restrictive permissions.

## Document Types

| Type | Handler | Behavior |
|---|---|---|
| `generic` (default) | `GenericDocumentHandler` | Standard extract-and-index via file-type extractors |
| `paper` | `PaperDocumentHandler` | pdf2bib bibliographic extraction, DOI embedding, title validation |
| `textbook` | `TextbookDocumentHandler` | Splits PDF into chapters via TOC/sidecar, each chapter indexed independently |
| `reference` | `ReferenceDocumentHandler` | Metadata-only entries without files; auto-generates BibTeX |

## Source Types

| Value | Applies To | Description |
|---|---|---|
| `file` | All types | Backed by a file on disk (implicit default) |
| `directory` | Textbooks, Papers | Directory-based with child files (chapters for textbooks, supplements for papers) |
| `reference` | Papers, Textbooks, Generic | Metadata-only entry (papers use `{citation_key}.bib` as relative path) |

## Key Components

### Core (`docsearch/core/`)

- **`models.py`** — `Document` (indexed doc with extracted + sidecar metadata, `document_type`, `source_type`; `combined_metadata` merges both), `Chapter` (textbook chapter; `combined_metadata()` inherits from parent), `Supplement` (paper supplement within directory-type paper; `combined_metadata()` inherits from parent), `TextRow` (frozen: stored text addressed by `kind` + `id` + `label`; uniformizes all tables for checks), `SearchResult` (doc + FTS score + optional chapter/supplement + snippet), `SearchQuery` (search params incl. `raw_fts` flag). `from_row()` supports `tuple`, `dict`, `sqlite3.Row`; `_json_object_or_empty()` degrades malformed columns to `{}`.

- **`repository.py`** — SQLite with WAL journal; **no file I/O by design** (Indexer owns filesystem). FTS5 on `filename`, `directory`, `full_text`. Dynamic SQL builder supporting scope, extension, author, tags, date range, `document_types` filters. Query helpers: `fts_match_query()` compiles free text into quoted FTS5 expression (operators searched not parsed, trailing `*` as prefix); `_scope_clause()` matches dir + subtree on path components; `_author_clause()` containment match across merged metadata (`author`/`authors`/`authors_bib`). Methods: `upsert`, `search`, `get`, `get_by_id`, `remove`, `count`, `all_paths`, `exists`, `list_directory` (infers subdirs from nested paths), `rename`, `update_document`, `update_sidecar_metadata` (read-modify-write, tolerates corrupt JSON), `iter_texts`. Chapter methods: `upsert_chapter`, `get_chapters`, `get_chapter`, `delete_chapters`, `delete_chapter_by_id`, `search_textbook_chapters`. Supplement methods: `upsert_supplement`, `get_supplements`, `get_supplement`, `delete_supplements`, `delete_supplement_by_id`, `update_supplement_metadata`, `search_paper_supplements`. Migration logic adds/renames columns.

- **`sidecars.py`** — Single source of truth for sidecar location and IO. `SIDECAR_SUFFIX` (`.meta.json`), `sidecar_path()` (directory textbooks use `<dir>/<dirname>.meta.json`), fault-tolerant `load_sidecar()` (`{}` on missing/corrupt/non-object), `write_sidecar()` (logs on IOError).

- **`repair.py`** — Registry of integrity checks over data this program wrote; user metadata is out of scope. `RepairCheck` has `name`, `description`, `scan(repo)`, `apply(repo, findings)`. `TextTransformCheck` subclasses need pure `transform()` returning `None` to leave value alone (idempotent). First check: `control-characters`.

- **`indexer.py`** — Orchestrates indexing (filesystem + DB). Calls `load_extractors()` for extension→extractor map. Delegates to `handlers.get_handler()`. SHA-256 change detection, loads `.meta.json` sidecars. `scan_directory()` full sync: new/changed/deleted. Short-circuits for `document_type="reference"`. `move_file()` moves file + DB entry. `attach_file()` attaches file to reference: renames path, writes metadata to sidecar, re-indexes with `skip_bib=True`. `convert_to_directory()` auto-converts file-type paper to directory-type when first supplement is attached (creates dir from filename, moves PDF as primary, writes sidecar). Metadata edits via `set_metadata_key()` / `delete_metadata_key()` write DB column + sidecar file together without re-extracting.

- **`handlers.py`** — Pipeline framework. Base `DocumentHandler` with `pre_process()`, `extract_metadata()`, `extract_text()`, `post_process()` hooks. All handlers set `doc.id` from `upsert` return value. Subclasses:
  - `GenericDocumentHandler` — default behavior
  - `PaperDocumentHandler` — dispatches file vs directory. File: DOI embedding via `pdf2doi`, pdf2bib extraction, strips JATS/HTML markup (`strip_html_markup()`), corroborates title against file before storing (`_title_appears_in_text()`), moves authors to `authors_bib`, stores raw bibtex in sidecar. Directory: `_handle_directory()` reads primary PDF from sidecar `"primary"` key (or auto-detects single PDF), runs pdf2bib on primary, indexes remaining files as supplements with text extraction. Falls back to `_generate_bibtex_from_metadata()` when `skip_bib=True`
  - `TextbookDocumentHandler` — dispatches on file vs directory. File: extracts PDF metadata, detects chapters via TOC/sidecar. Directory: enumerates files as chapters, `<dirname>.meta.json` sidecar, alphabetical default ordering; merges sidecar chapter data (incl. `"sections"`) into chapter metadata
  - `ReferenceDocumentHandler` — creates metadata-only entries with `source_type='reference'`, `{citation_key}.bib` as path, populates `full_text` from title/author/journal for searchability
  - Helpers: `strip_html_markup()`, `_normalize_title()` (NFKC + markup strip), `_titles_match()`, `_title_appears_in_text()`, `_format_author_dict()`, `_format_authors_bib()`, `_generate_bibtex_from_metadata()`

- **`slicing.py`** — Runtime line-slicing utilities for document sections. Pure functions (no DB/filesystem access): `split_lines()`, `slice_lines()` (comma-separated ranges), `get_section_text()` (single section), `get_sections_map()` (parse sidecar `sections` key), `reindex_sections()` (contiguous keys after deletion).

### Extractors (`docsearch/extractors/`)

All extend `BaseExtractor` (abstract: `supported_extensions`, `extract_metadata()`, `extract_text()`). Fault-tolerant — return empty on failure rather than raising.

`BaseExtractor.extract()` passes output through `sanitize_text()` (exported from `__init__.py`), which strips C0 controls except `\t`, `\n`, `\r`. PyMuPDF emits U+0000/U+0001 around glyph runs; embedded NUL makes SQLite `length()` report truncated values. Code paths bypassing extractors (`handlers._extract_pages()` for range chapters) sanitize explicitly.

Registry owned by `load_extractors()` in `extractors/__init__.py` → `extension → BaseExtractor` dict.

| Extractor | Extensions | Metadata Source |
|---|---|---|
| `PdfExtractor` | `pdf` | PyMuPDF (title, author, subject, creator, producer, dates, page count) |
| `DocxExtractor` | `docx` | python-docx custom properties |
| `MarkdownExtractor` | `md`, `markdown`, `txt` | PyYAML frontmatter |

### Sidecar Metadata

User-editable metadata in `<filepath>.meta.json`. Indexer reads separately from extractor-derived metadata; `combined_metadata` merges both (sidecar overrides).

**Two locations must agree:** `documents.sidecar_metadata` column (what search/read paths use) and `.meta.json` file (reloaded on re-index). Edits go through `Indexer.set_metadata_key()` / `delete_metadata_key()` which write both without re-extracting. They merge current column with current file first, so hand-edited keys survive. Works for reference entries (no file on disk).

**Value parsing (`-m KEY=VALUE`, `meta set -v`):** `parse_meta_value()`/`parse_meta_pairs()` in `cli/utils.py`. JSON tried first (numbers/lists/objects work); unparseable kept as string. Quoting forces strings: `-m arxiv_id='"1706.03762"'` — unquoted `1706.03762` silently becomes float.

### Database Schema

SQLite with:
- **`documents`** table: path (unique), filename, directory, extension, size, mtime, content_hash (SHA-256), extracted_metadata (JSON), sidecar_metadata (JSON), full_text, indexed_at, document_type (TEXT, default `'generic'`), source_type (TEXT, nullable)
- **`documents_fts`**: FTS5 on (filename, directory, full_text), unicode61 tokenizer, auto-sync triggers
- **`textbook_chapters`** table: id, textbook_id (FK→documents), chapter_index, title, chapter_type (`'range'`/`'file'`), start_page, end_page, page_count, file_path, metadata (JSON), full_text
- **`textbook_chapters_fts`**: FTS5 on (title, full_text), unicode61 tokenizer
- **`paper_supplements`** table: id, paper_id (FK→documents), supplement_index, title, file_path, metadata (JSON), full_text
- **`paper_supplements_fts`**: FTS5 on (title, full_text), unicode61 tokenizer
- Indexes on `directory`, `extension`, `textbook_id`, unique (textbook_id, chapter_index)

Migrations in `migrations/` are reference-only; schema embedded in `repository.py`'s `_SCHEMA_SQL`. Runtime migrations handle backward-compatible column additions/renames.

## CLI

Entry point: `docsearch` (maps to `docsearch.cli.main:cli`)

```
docsearch [--home PATH] COMMAND

Core:
  info [DOC_ID]           Show database info; with DOC_ID show full metadata
  search                  Search indexed documents (-q, --scope, --type, --author, --tag, --after/--before, --document-types, --raw-fts)
  get <DOC_ID>            Retrieve text content (-f text/json, --sections, --lines)
  bibtex <DOC_ID>         Export BibTeX for a paper
  ls [PATH]               List indexed directory contents (-f text/json)

Index:
  index scan <DIR>        Scan directory tree (-T TYPE, --no-recursive)
  index add <FILE>        Add single file to index
  index remove <FILE>     Remove from index
  index move <SRC> <DST>  Move indexed file (DST may be directory)

Metadata:
  meta show <FILE>        Display metadata (-k KEY for single field)
  meta set <FILE>         Set key on indexed document (-k KEY -v VALUE)
  meta delete <FILE>      Remove key from indexed document
  meta init <FILE>        Create empty sidecar file
  meta list-sections <FILE>   List document sections
  meta set-section <FILE>     Add section (--name, --start, --end)
  meta delete-section <FILE>  Delete section by index

References:
  reference               Register generic reference (-t TITLE, -a AUTHOR, -m KEY=VALUE)
  document attach <ID> <FILE>  Attach file to reference entry
  document detach <ID>    Detach file, converting to reference

Papers:
  papers add <FILE|DIR>    Add research paper (--doi, --skip-bib, --primary FILE for dirs)
  papers upload <FILE>     Upload and auto-index (-n NAME, -D DIR)
  papers reference         Register paper reference (-t TITLE, -a AUTHOR, -y YEAR, ...)
  papers list-supplements <ID>   List supplements for directory-type paper
  papers supplement <ID> <INDEX> Get supplement text (--sections, --lines, --list-sections, --set-section, --delete-section)
  papers attach-supplement <ID> <FILE>  Attach supplement (auto-converts file→directory)
  papers detach-supplement <ID> <INDEX> Remove supplement from directory paper

Textbooks:
  textbooks add <FILE>    Add textbook (-n NAME, -D DIR, -m KEY=VALUE)
  textbooks reference     Register textbook reference (-t TITLE, -a AUTHOR, ...)
  textbooks init <DIR>    Initialize directory-type textbook (-t TITLE)
  textbooks set-chapters <ID>   Redefine chapter breakpoints (-b BREAKPOINTS)
  textbooks attach-chapter <ID> <FILE>  Associate chapter file (-i INDEX)
  textbooks detach-chapter <ID> <INDEX>  Remove chapter from directory textbook
  textbooks chapters <FILE>   List indexed chapters
  textbooks chapter <FILE>    Print chapter text (-i INDEX, --sections, --lines, --list-sections, --set-section, --delete-section)

Repair:
  repair check            Report corruption (--check NAME, -v)
  repair apply            Repair in place (--check NAME, -v)
```

Search supports `-f FORMAT` (text/json/csv). `--author` reads merged metadata across `author`/`authors`/`authors_bib`. `--scope` matches directory + subtree on path components.

## REST API

Entry point: `docsearch-server` (maps to `docsearch.server.app:main`, uvicorn on `0.0.0.0:8000`)

Single `get_config()` dependency from `server/dependencies.py`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/search` | Search (params: q, scope, file_type, author, tags, after, before, document_types, raw_fts) |
| GET | `/api/fs` | List directory (`path` param) → `{entries, directories}` |
| POST | `/api/index/scan` | Scan dir `{dirpath, recursive?, document_type?, extra_metadata?}` |
| POST | `/api/index/add` | Add file `{filepath, document_type?, extra_metadata?}` |
| POST | `/api/index/remove` | Remove file `{filepath}` |
| POST | `/api/index/upload` | Upload + index (multipart, query: `directory`, `filename`) |
| GET | `/api/documents/{id}` | Get metadata |
| GET | `/api/documents/{id}/content` | Get text (`?lines=RANGES` for slicing) |
| GET | `/api/documents/{id}/file` | Download file (404 for references) |
| GET | `/api/documents/{id}/meta` | Get sidecar metadata |
| PATCH | `/api/documents/{id}/meta` | Update key `{key, value}` → writes column + `.meta.json` |
| GET | `/api/documents/{id}/bibtex` | Export BibTeX (papers only) |
| POST | `/api/documents/{id}/move` | Move `{destination}` |
| POST | `/api/documents/{id}/attach` | Attach file to reference (multipart) |
| POST | `/api/documents/{id}/detach` | Detach file → converts to reference |

**Sections:**

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/documents/{id}/sections` | List sections |
| POST | `/api/documents/{id}/sections` | Add section `{name, start, end?}` |
| GET | `/api/documents/{id}/sections/{index}` | Get section content |
| DELETE | `/api/documents/{id}/sections/{index}` | Delete section (re-indexes) |

**Textbooks:**

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/documents/textbooks/add` | Add textbook `{filepath, extra_metadata?}` |
| POST | `/api/documents/textbooks/upload` | Upload (multipart, query: `variant`, `chapter_breakpoints`) |
| GET | `/api/documents/{id}/chapters` | List chapters |
| GET | `/api/documents/{id}/chapters/{index}` | Get chapter |
| PUT | `/api/documents/{id}/chapters` | Redefine breakpoints `{breakpoints}` |
| DELETE | `/api/documents/{id}/chapters/{index}` | Delete directory-type chapter |
| POST | `/api/documents/{id}/chapters/upload` | Upload chapter (directory textbooks) |

**Chapter sections:**

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/documents/textbooks/{id}/chapters/{idx}/sections` | List chapter sections |
| POST | `/api/documents/textbooks/{id}/chapters/{idx}/sections` | Add section `{name, start, end?}` |
| GET | `/api/documents/textbooks/{id}/chapters/{idx}/sections/{sidx}` | Get section content |
| DELETE | `/api/documents/textbooks/{id}/chapters/{idx}/sections/{sidx}` | Delete section (re-indexes) |

**Papers:**

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/documents/papers/add` | Add paper `{filepath, doi?, skip_bib?}` |
| POST | `/api/documents/papers/upload` | Upload paper (multipart) |
| POST | `/api/documents/papers/reference` | Register reference `{title, author?, year?, ...}` |

**Supplements:**

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/documents/{id}/supplements` | List supplements |
| GET | `/api/documents/{id}/supplements/{index}` | Get supplement (`?lines=RANGES`, `?section=INDEX`) |
| POST | `/api/documents/{id}/supplements/upload` | Upload supplement (auto-converts file→directory) |
| DELETE | `/api/documents/{id}/supplements/{index}` | Delete supplement |

**Supplement sections:**

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/documents/{id}/supplements/{idx}/sections` | List supplement sections |
| POST | `/api/documents/{id}/supplements/{idx}/sections` | Add section `{name, start, end?}` |
| GET | `/api/documents/{id}/supplements/{idx}/sections/{sidx}` | Get section content |
| DELETE | `/api/documents/{id}/supplements/{idx}/sections/{sidx}` | Delete section (re-indexes) |

**Chapter breakpoints** (`chapter_breakpoints`, file-type only): list `[5,10]` (N boundaries → N+1 chapters) or dict `{"Intro":5,"Methods":null}` (keys=titles, values=end pages; `null`=to end). First chapter starts at page 0.

**Directory textbooks** (`variant=directory`) require `filename` query param — determines directory name and default title.

## Tests

Located in `tests/`, run with `pytest`.

| File | Coverage |
|---|---|
| `test_repository.py` | Document model, Repository (upsert, search with all filters, get_by_id, exists, list_directory incl. reference docs and directory textbooks) |
| `test_search_filters.py` | Query compilation (quoting/AND, operators searched not parsed, trailing-`*` prefix), author over merged metadata (sidecar list/string/dicts, partial names, corrupt column tolerated) |
| `test_extractors.py` | PdfExtractor (metadata, text, multi-page, fault tolerance) |
| `test_handlers.py` | BibTeX helpers, strip_html_markup, title normalization + corroboration, PaperDocumentHandler integration |
| `test_server.py` | REST API endpoints: content/file, upload, BibTeX, papers, textbooks, chapters, directory textbooks, references, filesystem browsing, attach/detach, PATCH /meta |
| `test_sections.py` | Document sections: slicing utilities, API (list/add/get/delete sections, content?lines=), CLI (list-sections, set-section, delete-section, get --sections/--lines) |
| `test_textbook_chapters.py` | Chapter management: PUT breakpoints (list/dict), DELETE chapter, CLI set-chapters and detach-chapter |
| `test_paper_supplements.py` | Directory-type papers: Supplement model, repository CRUD, handler directory handling, auto-conversion (file→directory), API endpoints (list/get/upload/delete/sections), CLI commands (list-supplements, supplement with section flags, attach/detach) |
| `test_cli_path_resolution.py` | CLI path resolution from subdirectories, index move with directory destinations |
| `test_papers_cli_path_resolution.py` | papers add / textbooks add from subdirectories |
| `test_sanitization.py` | sanitize_text (C0 strip, tab/newline preserved), applied via extractors and _extract_pages |
| `test_metadata_updates.py` | Indexer metadata edits (both stores updated, survives re-index, no re-extraction), CLI meta commands, value parsing |
| `test_repair.py` | control-characters check (detect/repair NUL, SQL length matches, FTS still works, idempotent) |
| `conftest.py` | Shared fixtures: sample PDFs generated via PyMuPDF |

**Coverage gaps:** DocxExtractor, MarkdownExtractor; CLI search/get/bibtex/ls/info/reference commands (Click testing); Config class; Indexer.scan_directory() full sync.

## Conventions

- All extractors fault-tolerant (catch exceptions, return empty results)
- Content-hash change detection avoids unnecessary re-indexing
- Extractor registry: `extractors/__init__.py` (`load_extractors()`). Handler registry: `core/handlers.py` (`get_handler()`)
- Database home explicit; all paths relative to it (`Config` class)
- Single shared `get_config()` dependency across server routes
- **Type checking not enforced.** `mypy strict = true` in `pyproject.toml` for ad-hoc use only (~190 pre-existing errors). Verify with pytest, not mypy.
- Migration SQL in `migrations/` is reference-only (schema embedded in repository.py)
- Test PDFs generated programmatically (no binary fixtures committed)
- References use `{citation_key}.bib` as relative path for uniqueness

## Known Limitations

- CLI path resolution resolves against cwd first; API has no cwd concept
- **Snippet support:** `SearchResult.snippet` never populated (always empty string)
- **Migration runner:** migration files are reference-only, no automated execution
- **Sanitization forward-only:** existing text keeps control chars until `docsearch repair apply`
- **Repair scope deliberate:** only covers corruption this program wrote; user metadata never rewritten
- **FTS plain text by default:** `fts_match_query()` neutralizes operators; interior `*`, `col:`, `NEAR()`, `^boost`, bare `OR`/`AND` need `--raw-fts` / `raw_fts=true`. Trailing `*` honored as prefix.
