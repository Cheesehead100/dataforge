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
from dataforge.parsing.adf_importer import AdfImporter, AdfImportError
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
@click.option("--resource-group", default="rg-dataforge", show_default=True)
@click.option("--env", type=click.Choice(["dev", "test", "prod"]), default="dev", show_default=True)
@click.option(
    "--compare-azure",
    is_flag=True,
    help="Compare planned RBAC against current role assignments in Azure (requires az CLI login)",
)
@click.option("--subscription-id", default=None, help="Azure subscription ID for --compare-azure")
def plan(
    description: str,
    region: str,
    resource_group: str,
    env: str,
    compare_azure: bool,
    subscription_id: str | None,
) -> None:
    """Parse a description and show the planned RBAC assignments without writing files.

    With --compare-azure, fetches existing role assignments from Azure and
    shows a diff (approximate — planned refs are expressions, not resolved IDs).

    \b
    Example:
        dataforge plan "ADF reads ADLS and triggers Databricks" --compare-azure --resource-group rg-myapp
    """
    try:
        settings = get_settings()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    overrides = {"location": region, "environment": env, "resource_group": resource_group}

    with console.status("[bold cyan]Parsing description…"):
        try:
            graph = IntentParser(client, settings).parse(description, overrides)
        except ParseError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

    rbac = RbacResolver().resolve(graph)

    # ── Nodes summary ─────────────────────────────────────────────────────────
    console.print("\n[bold]Nodes detected:[/bold]")
    for n in graph.nodes:
        console.print(f"  [{n.id}] {n.type.value}: {n.name}")

    # ── Planned RBAC table ───────────────────────────────────────────────────
    _print_rbac_plan(rbac)

    if rbac.warnings:
        for w in rbac.warnings:
            console.print(f"  [yellow]WARN[/yellow] {w}")

    console.print(f"\n[bold]Planned:[/bold] {len(rbac.assignments)} role assignments, {len(rbac.unresolved)} unresolved")

    # ── Azure comparison ──────────────────────────────────────────────────────
    if compare_azure:
        _compare_with_azure(rbac, resource_group, subscription_id)


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


@cli.command("import-adf")
@click.argument("json_file", type=click.Path(exists=True, path_type=Path))
@click.option("--output-json", is_flag=True, help="Print FlowGraph as JSON (pipe to dataforge generate)")
@click.option("--generate", "do_generate", is_flag=True, help="Also generate Terraform from the imported graph")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("./output"), show_default=True)
@click.option("--overwrite", is_flag=True)
def import_adf(
    json_file: Path,
    output_json: bool,
    do_generate: bool,
    output: Path,
    overwrite: bool,
) -> None:
    """Import an ADF ARM export JSON and convert it to a DataForge FlowGraph.

    Parses linked services and pipeline activities into nodes and edges.
    Use --output-json to print the FlowGraph for inspection.
    Use --generate to immediately render Terraform from the imported graph.

    \b
    Example:
        dataforge import-adf factory-export.json --output-json
        dataforge import-adf factory-export.json --generate -o ./tf-output
    """
    try:
        graph = AdfImporter().import_from_file(json_file)
    except AdfImportError as exc:
        console.print(f"[red]Import failed:[/red] {exc}")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]Imported FlowGraph[/bold]: {len(graph.nodes)} nodes · {len(graph.edges)} edges\n"
            + "\n".join(f"  - {n.type.value}: [cyan]{n.name}[/cyan]" for n in graph.nodes),
            title=f"ADF Import — {json_file.name}",
            border_style="blue",
        )
    )

    if graph.edges:
        console.print("[bold]Edges:[/bold]")
        for e in graph.edges:
            console.print(f"  {e.source} -[{e.operation}]-> {e.target}")

    if output_json:
        print(graph.model_dump_json(indent=2))

    if do_generate:
        rbac = RbacResolver().resolve(graph)
        _print_rbac_plan(rbac)

        # Template-only generation — no API key required (llm_polish=False, client=None)
        result = HclGenerator(Renderer(), None, None).generate(graph, rbac, llm_polish=False)

        try:
            paths = OutputWriter().write(result.files, output, overwrite=overwrite)
        except FileExistsError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

        console.print(f"\n[green]OK[/green] Wrote {len(paths)} files to [cyan]{output}/[/cyan]")
        for p in paths:
            console.print(f"  {p.name}")

        if result.warnings:
            for w in result.warnings:
                console.print(f"  [yellow]WARN[/yellow] {w}")


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


def _compare_with_azure(rbac, resource_group: str, subscription_id: str | None) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    console.print("\n[bold cyan]Fetching existing Azure role assignments…[/bold cyan]")
    cmd = ["az", "role", "assignment", "list", "--resource-group", resource_group, "--output", "json"]
    if subscription_id:
        cmd += ["--subscription", subscription_id]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        console.print("[yellow]WARN[/yellow] 'az' CLI not found. Install Azure CLI to use --compare-azure.")
        return
    except subprocess.TimeoutExpired:
        console.print("[red]Timeout fetching Azure role assignments.[/red]")
        return

    if proc.returncode != 0:
        console.print(f"[red]az CLI error:[/red] {proc.stderr.strip()}")
        return

    try:
        existing: list[dict] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        console.print("[red]Could not parse az CLI output.[/red]")
        return

    # Index existing roles by role definition name (display name)
    existing_roles: set[str] = {
        a.get("roleDefinitionName", "") for a in existing if a.get("roleDefinitionName")
    }
    planned_roles: set[str] = {ra.role_name for ra in rbac.assignments}

    already_covered = planned_roles & existing_roles
    not_yet_created = planned_roles - existing_roles
    unmanaged_in_rg = existing_roles - planned_roles

    table = Table(title=f"RBAC Delta — {resource_group}", show_header=True, header_style="bold")
    table.add_column("Status", width=10)
    table.add_column("Role Name")
    table.add_column("Note", style="dim")
    for r in sorted(not_yet_created):
        table.add_row("[yellow]+ PLANNED[/yellow]", r, "DataForge will create this")
    for r in sorted(already_covered):
        table.add_row("[green]= EXISTS[/green]", r, "Already present in resource group")
    for r in sorted(unmanaged_in_rg):
        table.add_row("[dim]? EXTERNAL[/dim]", r, "Exists but not in DataForge plan")
    console.print(table)
    console.print(
        "[dim]Note: comparison is by role name only — scopes and principals differ per resource.[/dim]"
    )


def _configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
