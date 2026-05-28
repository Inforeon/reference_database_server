from __future__ import annotations

import json
import shutil
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="document")
def document() -> None:
    """Document-level operations (attach, detach)."""
    pass


@document.command()
@click.argument("doc_id", type=int)
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def attach(ctx: dict, doc_id: int, filepath: str) -> None:
    """Attach a local file to an existing reference entry.

    Converts the entry from ``source_type='reference'`` to ``source_type='file'``.
    Preserves existing metadata by writing it to a sidecar so it takes precedence
    over any conflicting metadata extracted from the file.
    """
    config = ctx["config"]
    root = config.home

    # Resolve source file path
    src_p = Path(filepath).resolve()

    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document {doc_id} not found.", err=True)
            return
        if doc.source_type != "reference":
            click.echo(
                f"Document is not a reference entry (source_type={doc.source_type!r}). "
                "Only reference entries can have a file attached.",
                err=True,
            )
            return

        # Copy the physical file into the database home
        # Use the same directory structure as the original reference path, or root if empty
        doc_dir = Path(doc.path).parent
        target_dir = root / doc_dir if str(doc_dir) != "." else root
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / src_p.name

        shutil.copy2(str(src_p), str(target_path))

        rel_target = str(target_path.relative_to(root))

        # Delegate to indexer: rename DB path → write sidecar → extract
        indexer = Indexer(repo, config.home)
        new_doc = indexer.attach_file(
            rel_target,
            doc_id,
            document_type=doc.document_type,
            existing_metadata=doc.combined_metadata or None,
        )
        if new_doc is None:
            click.echo("Failed to index attached file.", err=True)
            return

        # Update source_type to "file"
        repo.update_document(doc_id, source_type="file")

        click.echo(f"Attached: {src_p} → {new_doc.path}")
    finally:
        repo.close()


@document.command()
@click.argument("doc_id", type=int)
@click.pass_obj
def detach(ctx: dict, doc_id: int) -> None:
    """Detach the physical file from a document.

    Converts the entry to ``source_type='reference'``. Deletes the main file
    but preserves the sidecar (.meta.json) so user-editable metadata survives.
    Clears full_text and extracted_metadata in the database.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document {doc_id} not found.", err=True)
            return
        if doc.source_type == "reference":
            click.echo("Document is already a reference entry (no file to detach).", err=True)
            return
        if doc.source_type == "directory":
            click.echo(
                "Cannot detach a directory-type document. "
                "This operation is only supported for file-backed documents.",
                err=True,
            )
            return

        abs_path = config.home / doc.path

        # Delete the main file
        if abs_path.is_file():
            abs_path.unlink()

        # Preserve the sidecar — do NOT delete it

        # Clear extractable content in the DB (no file → nothing to extract)
        repo.update_document(
            doc_id,
            source_type="reference",
            full_text="",
            extracted_metadata={},
        )

        click.echo(f"Detached: {doc.path} (sidecar preserved)")
    finally:
        repo.close()
