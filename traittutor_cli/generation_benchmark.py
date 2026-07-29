"""CLI entry point for the offline TraitTutor generation benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from traittutor.generate.benchmark import run_benchmark, write_benchmark_report


def register(app: typer.Typer) -> None:
    @app.command("benchmark")
    def benchmark(
        output: Path | None = typer.Option(
            None,
            "--output",
            "-o",
            help="Write the JSON report to this path as well as stdout.",
        ),
    ) -> None:
        """Run fixed anonymous generation-quality fixtures and print JSON."""

        report = run_benchmark()
        if output is not None:
            write_benchmark_report(report, output)
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["summary"]["passed"]:
            raise typer.Exit(code=1)
