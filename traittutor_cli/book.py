"""``traittutor book ...`` CLI commands for the new BookEngine.

Currently exposes maintenance commands. (Authoring/reading still goes through
the API + web frontend.)
"""

from __future__ import annotations

import json

import typer

from .common import console


def register(app: typer.Typer) -> None:
    @app.command("list")
    def list_books() -> None:
        """List all books in the local workspace."""
        from traittutor.book import get_book_engine

        engine = get_book_engine()
        books = engine.list_books()
        if not books:
            console.print("[yellow]No books yet.[/yellow]")
            return
        for book in books:
            stale = len(book.stale_page_ids or [])
            stale_label = f" [red]({stale} stale)[/red]" if stale else ""
            console.print(
                f"[bold]{book.title or '(untitled)'}[/bold]  "
                f"[dim]{book.id}[/dim]  "
                f"[cyan]{book.status.value}[/cyan]"
                f"{stale_label}"
            )

    @app.command("health")
    def health(
        book_id: str = typer.Argument(..., help="Book id."),
    ) -> None:
        """Inspect KB drift + log.md health for a book."""
        from traittutor.book import get_book_engine

        engine = get_book_engine()
        drift = engine.kb_drift_report(book_id)
        log = engine.log_health(book_id)
        console.print_json(json.dumps({"kb_drift": drift, "log_health": log}))

    @app.command("refresh-fingerprints")
    def refresh_fingerprints(
        book_id: str = typer.Argument(..., help="Book id."),
    ) -> None:
        """Re-snapshot KB fingerprints; clears the stale-page list."""
        from traittutor.book import get_book_engine

        engine = get_book_engine()
        result = engine.refresh_kb_fingerprints(book_id)
        if result is None:
            console.print(f"[red]Book {book_id} not found.[/red]")
            raise typer.Exit(code=1)
        console.print_json(json.dumps(result))
