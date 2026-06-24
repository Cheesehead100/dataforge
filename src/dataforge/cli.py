"""DataForge CLI — the single user-facing entry point."""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows regardless of the active code page.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import anthropic
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dataforge import __version__
from dataforge.config import Settings, get_settings
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.flow_graph import FlowMetadata
from dataforge.output.writer import OutputWriter
from dataforge.parsing.intent_parser import IntentParser, ParseError
from dataforge.rbac.resolver import RbacResolver
from dataforge.validation.checkov_runner import CheckovRunner

console = Console(legacy_windows=False)  # use Python I/O path; avoids CP1252 LegacyWindowsTerm


@click.group()
@click.version_option(__version__, prog_name="dataforge")
def cli() -> None:
    """DataForge — Intent-to-Infrastructure for Azure data engineering stacks.

    Converts natural-language data flow descriptions into production-ready
    Terraform (ADF / Databricks / Fabric / ADLS / Key Vault) with automatic
    RBAC role assignment wiring.
    """


@cli.command()
@click.argument("description")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("./output"), show_default=True)
@click.option("--region", default="eastus", show_default=True, help="Azure region")
@click.option("--resource-group", default="rg-dataforge", show_default=True)
@click.option("--env", type=click.Choice(["dev", "test", "prod"]), default="dev", show_default=True)
@click.option("--app-name", default="dataforge", show_default=True, help="Application name used in resource naming")
@click.option("--no-validate", is_flag=True, help="Skip Checkov validation")
@click.option("--no-llm-polish", is_flag=True, help="Skeleton only - no Sonnet polish pass")
@click.option("--overwrite", is_flag=True, help="Overwrite existing output directory")
@click.option("--dry-run", is_flag=True, help="Print FlowGraph + RBAC plan without writing files")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON to stdout")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v, -vv)")
def generate(
    description: str,
    output: Path,
    region: str,
    resource_group: str,
    env: str,
    app_name: str,
    no_validate: bool,
    no_llm_polish: bool,
    overwrite: bool,
    dry_run: bool,
    json_output: bool,
    verbose: int,
) -> None:
    """Generate Terraform from a natural-language data flow description.

    \b
    Example:
        dataforge generate "Read Parquet from ADLS, transform in Databricks, write to Fabric Lakehouse"
    """
    _configure_logging(verbose)

    try:
        settings = get_settings()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("Set ANTHROPIC_API_KEY in your environment or .env file.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    metadata_overrides: dict = {
        "location": region,
        "resource_group": resource_group,
        "environment": env,
        "application_name": app_name,
        "tags": {"managed-by": "dataforge", "environment": env},
    }

    with console.status("[bold cyan]Parsing description with Claude Haiku…"):
        try:
            graph = IntentParser(client, settings).parse(description, metadata_overrides)
        except ParseError as exc:
            console.print(f"[red]Parse failed:[/red] {exc}")
            sys.exit(1)

    console.print(
        Panel(
            f"[bold]FlowGraph[/bold]: {len(graph.nodes)} nodes · {len(graph.edges)} edges\n"
            + "\n".join(f"  - {n.type.value}: [cyan]{n.name}[/cyan]" for n in graph.nodes),
            title="Parsed",
            border_style="green",
        )
    )

    rbac = RbacResolver().resolve(graph)
    _print_rbac_plan(rbac)

    if dry_run:
        console.print("[yellow]Dry run — no files written.[/yellow]")
        return

    if no_llm_polish:
        result = HclGenerator(Renderer(), None, settings).generate(graph, rbac, llm_polish=False)
    else:
        with console.status("[bold cyan]Generating Terraform with Claude Sonnet…"):
            result = HclGenerator(Renderer(), client, settings).generate(graph, rbac, llm_polish=True)

    try:
        paths = OutputWriter().write(result.files, output, overwrite=overwrite)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(f"\n[green]OK[/green] Wrote {len(paths)} files to [cyan]{output}/[/cyan]:")
    for p in paths:
        console.print(f"  {p.name}")

    if result.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]WARN[/yellow] {w}")

    if not no_validate:
        with console.status("[bold cyan]Running Checkov…"):
            report = CheckovRunner().run(output)
        _print_checkov_report(report)

    if json_output:
        out = {"files": [f.filename for f in result.files], "warnings": result.warnings}
        print(json.dumps(out, indent=2))


@cli.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
def validate(directory: Path) -> None:
    """Run Checkov on an existing Terraform directory."""
    report = CheckovRunner().run(directory)
    _print_checkov_report(report)
    sys.exit(0 if report.ok else 1)


@cli.command()
@click.argument("description")
@click.option("--region", default="eastus", show_default=True)
@click.option("--env", default="dev", show_default=True)
def explain(description: str, region: str, env: str) -> None:
    """Parse a description and show the FlowGraph + RBAC plan without writing files."""
    try:
        settings = get_settings()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    overrides = {"location": region, "environment": env}

    with console.status("[bold cyan]Parsing…"):
        try:
            graph = IntentParser(client, settings).parse(description, overrides)
        except ParseError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

    rbac = RbacResolver().resolve(graph)

    console.print("\n[bold]Nodes:[/bold]")
    for n in graph.nodes:
        console.print(f"  [{n.id}] {n.type.value}: {n.name}")
        if n.properties:
            for k, v in n.properties.items():
                console.print(f"         {k}={v}")

    console.print("\n[bold]Edges:[/bold]")
    for e in graph.edges:
        console.print(f"  {e.source} -[{e.operation}]-> {e.target}")

    _print_rbac_plan(rbac)


# ── helpers ────────────────────────────────────────────────────────────────

def _print_rbac_plan(rbac) -> None:  # type: ignore[no-untyped-def]
    table = Table(title="RBAC Assignments", show_header=True, header_style="bold magenta")
    table.add_column("Principal", style="cyan")
    table.add_column("Role")
    table.add_column("Scope", style="green")
    table.add_column("Operation", style="dim")
    for ra in rbac.assignments:
        table.add_row(ra.principal_node_id, ra.role_name, ra.scope_node_id, ra.operation)
    console.print(table)

    if rbac.warnings:
        for w in rbac.warnings:
            console.print(f"  [yellow]WARN[/yellow] {w}")


def _print_checkov_report(report) -> None:  # type: ignore[no-untyped-def]
    status = "[green]PASS[/green]" if report.ok else "[red]FAIL[/red]"
    console.print(
        f"\nCheckov: {status} | {report.passed} passed / {report.failed} failed / {report.skipped} skipped"
    )
    if report.failed_checks:
        for f in report.failed_checks[:10]:
            console.print(f"  [red]FAIL[/red] {f.check_id} {f.severity} - {f.resource} ({f.file_path})")
        if len(report.failed_checks) > 10:
            console.print(f"  … and {len(report.failed_checks) - 10} more")


def _configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
