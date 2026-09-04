from __future__ import annotations

from pathlib import Path

import click

from docsearch.cli.utils import parse_meta_pairs, resolve_user_path_to_home_relative
from docsearch.core.indexer import Indexer
from docsearch.core.models import Supplement
from docsearch.core.repository import Repository
from docsearch.core import slicing


@click.group(name="papers")
def papers() -> None:
    """Manage research papers (add, upload, reference, export bibtex)."""
    pass


@papers.command()
@click.argument("filepath")
@click.option("-d", "--doi", help="DOI to embed into the PDF before bibliographic extraction.")
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing (generate bibtex from available metadata only).")
@click.option("--primary", help="Primary paper filename within a directory (for directory-type papers).")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m arxiv_id='\"1706.03762\"'.",
)
@click.pass_obj
def add(ctx: dict, filepath: str, doi: str | None, skip_bib: bool, primary: str | None, meta_pairs: tuple[str, ...]) -> None:
    """Add a research paper to the index.

    Accepts a single PDF file or a directory of papers with supplementary
    material. For directories, use ``--primary`` to specify which file is the
    main paper (auto-detected if only one PDF exists).

    If ``--doi`` is provided it will be embedded into the PDF before running
    pdf2bib, ensuring correct bibliographic resolution.  Use ``--skip-bib`` to
    bypass pdf2bib entirely.
    """
    config = ctx["config"]
    # Allow directories for paper type
    p = Path(filepath).resolve()
    if p.is_dir():
        rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=False)
    else:
        rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = parse_meta_pairs(meta_pairs) or {}
        if doi:
            extra_meta["doi"] = doi
        if primary:
            extra_meta["primary"] = primary

        doc = indexer.add_file(rel_filepath, document_type="paper", extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type}, source={doc.source_type or 'file'})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        repo.close()


@papers.command()
@click.argument("file", type=click.File("rb"))
@click.option("-n", "--name", help="Filename to save as (default: original name).")
@click.option("-D", "--directory", default="", help="Subdirectory within database home to save into.")
@click.option("-d", "--doi", help="DOI to embed into the PDF before bibliographic extraction.")
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing.")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m arxiv_id='\"1706.03762\"'.",
)
@click.pass_obj
def upload(ctx: dict, file, name: str | None, directory: str, doi: str | None, skip_bib: bool, meta_pairs: tuple[str, ...]) -> None:
    """Upload a research paper and index it automatically."""
    import shutil
    config = ctx["config"]

    target_dir = config.home / directory if directory else config.home
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(config.home)):
        click.echo("Directory must be within the database home.", err=True)
        return

    if not target_dir.is_dir():
        click.echo(f"Directory does not exist: {target_dir}", err=True)
        return

    original_name = Path(file.name).name if hasattr(file, "name") and file.name else "uploaded.pdf"
    filename = name or original_name
    target_path = target_dir / filename

    with open(target_path, "wb") as f:
        shutil.copyfileobj(file, f)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = parse_meta_pairs(meta_pairs) or {}
        if doi:
            extra_meta["doi"] = doi

        rel_target = str(target_path.relative_to(config.home))
        doc = indexer.add_file(rel_target, document_type="paper", extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Uploaded & indexed: {doc.path}")
        else:
            click.echo(f"Failed to index uploaded file: {target_path}", err=True)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        repo.close()


@click.command(name="reference")
@click.option("-t", "--title", required=True, help="Title of the reference (required).")
@click.option("-a", "--author", default=None, help="Author string.")
@click.option("-y", "--year", default=None, help="Year of publication.")
@click.option("-j", "--journal", default=None, help="Journal name.")
@click.option("-b", "--booktitle", default=None, help="Book/proceedings name.")
@click.option("-d", "--doi", default=None, help="DOI string.")
@click.option("-u", "--url", default=None, help="URL string.")
@click.option("-k", "--citation-key", default=None, help="BibTeX citation key.")
@click.option(
    "-p", "--path", "filepath", default="",
    help="Path for grouping (file need not exist yet).",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m arxiv_id='\"1706.03762\"'.",
)
@click.pass_obj
def reference(ctx: dict, title: str, author: str | None, year: str | None, journal: str | None,
              booktitle: str | None, doi: str | None, url: str | None, citation_key: str | None,
              filepath: str, meta_pairs: tuple[str, ...]) -> None:
    """Register a metadata-only paper reference (no file required).

    Creates an index entry with ``source_type='reference'`` from supplied
    metadata. BibTeX is auto-generated if not provided via ``-m bibtex=...``.

    The ``--path`` option sets a real path for grouping within the database
    home; the file need not exist. If placed at that path later, a normal
    ``papers add`` will enrich the entry in-place.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = parse_meta_pairs(meta_pairs) or {}
        extra_meta["title"] = title
        if author:
            extra_meta["author"] = author
        if year:
            extra_meta["year"] = year
        if journal:
            extra_meta["journal"] = journal
        if booktitle:
            extra_meta["booktitle"] = booktitle
        if doi:
            extra_meta["doi"] = doi
        if url:
            extra_meta["url"] = url
        if citation_key:
            extra_meta["citation_key"] = citation_key

        doc = indexer.add_reference(filepath, document_type="paper", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Reference registered: {doc.path} (type={doc.document_type})")
        else:
            click.echo("Failed to create reference.", err=True)
    finally:
        repo.close()


# Register the reference command with the papers group (defined above via @click.command)
papers.add_command(reference)


@papers.command(name="list-supplements")
@click.argument("doc_id", type=int)
@click.pass_obj
def list_supplements(ctx: dict, doc_id: int) -> None:
    """List supplements for a directory-type paper."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"No document found with id {doc_id}", err=True)
            return
        if doc.document_type != "paper":
            click.echo(f"Document {doc_id} is not a paper", err=True)
            return

        supplements = repo.get_supplements(doc_id)
        if not supplements:
            click.echo(f"No supplements for {doc.path}")
            return

        click.echo(f"Supplements for {doc.path}:")
        for sup in supplements:
            sections = slicing.get_sections_map(sup.metadata)
            sec_info = f" ({len(sections)} sections)" if sections else ""
            click.echo(f"  [{sup.supplement_index}] {sup.title}{sec_info} ({sup.file_path})")
    finally:
        repo.close()


@papers.command()
@click.argument("doc_id", type=int)
@click.argument("index", type=int)
@click.option("-s", "--sections", help="Comma-separated section indices to retrieve.")
@click.option("--lines", help="Comma-separated line ranges (e.g. '0-99,200-299').")
@click.option("--list-sections", "-L", is_flag=True, help="List sections for this supplement.")
@click.option("--set-section", type=(str, int, int), nargs=3, metavar="NAME START END", help="Add a section (NAME START_LINE END_LINE).")
@click.option("--delete-section", "-D", type=int, metavar="INDEX", help="Delete a section by index.")
@click.pass_obj
def supplement(ctx: dict, doc_id: int, index: int, sections: str | None, lines: str | None,
               list_sections: bool, set_section: tuple[str, int, int] | None,
               delete_section: int | None) -> None:
    """Get or manage a supplement by index.

    Retrieve supplement text, optionally sliced by section indices (``--sections``)
    or line ranges (``--lines``). Manage sections with ``--list-sections``,
    ``--set-section``, and ``--delete-section``.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"No document found with id {doc_id}", err=True)
            return

        sup = repo.get_supplement(doc_id, index)
        if not sup:
            click.echo(f"No supplement at index {index} for {doc.path}", err=True)
            return

        # Section management operations
        if list_sections:
            sec_list = slicing.get_sections_map(sup.metadata)
            if not sec_list:
                click.echo(f"No sections defined on supplement '{sup.title}'")
                return
            lines_all = slicing.split_lines(sup.full_text)
            click.echo(f"Sections for supplement [{index}] {sup.title}:")
            for sec in sec_list:
                end_label = sec["end"] if sec["end"] is not None else "end"
                count = (sec["end"] if sec["end"] is not None else len(lines_all)) - sec["start"] + 1
                click.echo(f"  [{sec['index']}] {sec['name']} (lines {sec['start']}–{end_label}, {count} lines)")
            return

        if set_section:
            name, start, end = set_section
            indexer = Indexer(repo, config.home)
            # Get current metadata and add section
            meta = dict(sup.metadata)
            current_sections = meta.get("sections", {})
            # Find next available index
            existing_indices = [int(k) for k in current_sections.keys() if k.isdigit()]
            new_idx = max(existing_indices, default=-1) + 1
            current_sections[str(new_idx)] = {"name": name, "start": start, "end": end}
            meta["sections"] = current_sections
            # Update in DB
            repo.update_supplement_metadata(sup.id, meta)
            click.echo(f"Added section '{name}' at index {new_idx}")
            return

        if delete_section is not None:
            indexer = Indexer(repo, config.home)
            meta = dict(sup.metadata)
            sections_dict = meta.get("sections", {})
            if str(delete_section) not in sections_dict:
                available = list(sections_dict.keys()) if sections_dict else "(none)"
                click.echo(f"Section {delete_section} not found. Available: {available}", err=True)
                return
            del sections_dict[str(delete_section)]
            reindexed = slicing.reindex_sections(sections_dict)
            if reindexed:
                meta["sections"] = reindexed
                repo.update_supplement_metadata(sup.id, meta)
            else:
                meta.pop("sections", None)
                repo.update_supplement_metadata(sup.id, meta)
            click.echo(f"Deleted section {delete_section}")
            return

        # Text retrieval
        lines_all = slicing.split_lines(sup.full_text)

        if sections:
            parts = []
            for idx_str in sections.split(","):
                idx = int(idx_str.strip())
                sec_list = slicing.get_sections_map(sup.metadata)
                sec = next((s for s in sec_list if s["index"] == idx), None)
                if not sec:
                    click.echo(f"Section {idx} not found", err=True)
                    return
                text = slicing.get_section_text(lines_all, sec)
                parts.append(text)
            click.echo("\n".join(parts))
        elif lines:
            text = slicing.slice_lines(lines_all, lines)
            click.echo(text)
        else:
            click.echo(sup.full_text)
    finally:
        repo.close()


@papers.command(name="attach-supplement")
@click.argument("doc_id", type=int)
@click.argument("filepath")
@click.option("-i", "--index", type=int, default=None, help="Supplement index (auto-assign if omitted).")
@click.option("-n", "--name", default=None, help="Display name for the supplement.")
@click.pass_obj
def attach_supplement(ctx: dict, doc_id: int, filepath: str, index: int | None, name: str | None) -> None:
    """Attach a supplementary file to a paper.

    Auto-converts file-type papers to directory-type if needed.
    """
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.convert_to_directory(doc_id, rel_filepath, name)
        if doc:
            click.echo(f"Supplement attached to {doc.path}")
        else:
            click.echo("Failed to attach supplement.", err=True)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    finally:
        repo.close()


@papers.command(name="detach-supplement")
@click.argument("doc_id", type=int)
@click.argument("index", type=int)
@click.pass_obj
def detach_supplement(ctx: dict, doc_id: int, index: int) -> None:
    """Remove a supplement from a directory-type paper by index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"No document found with id {doc_id}", err=True)
            return
        if doc.source_type != "directory":
            click.echo(f"Document {doc_id} is not a directory-type paper", err=True)
            return

        sup = repo.get_supplement(doc_id, index)
        if not sup:
            click.echo(f"No supplement at index {index}", err=True)
            return

        # Delete from DB
        repo.delete_supplement_by_id(sup.id)

        # Remove physical file if it exists
        from docsearch.core.sidecars import sidecar_path
        dir_p = Path(config.home) / doc.path
        if sup.file_path and (dir_p / sup.file_path).is_file():
            (dir_p / sup.file_path).unlink()

        # Update sidecar to remove supplement entry and reindex
        from docsearch.core.sidecars import load_sidecar, write_sidecar
        sidecar = sidecar_path(dir_p, "directory")
        meta = load_sidecar(sidecar)
        supplements = meta.get("supplements", {})
        if str(index) in supplements:
            del supplements[str(index)]
            # Reindex supplement keys
            reindexed = {}
            for new_i, (old_key, val) in enumerate(sorted(supplements.items(), key=lambda x: int(x[0]))):
                reindexed[str(new_i)] = val
            meta["supplements"] = reindexed
            write_sidecar(sidecar, meta)

        click.echo(f"Removed supplement [{index}] '{sup.title}'")
    finally:
        repo.close()
