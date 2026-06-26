"""
DataForge CLI — the single user-facing entry point for all terminal workflows.

Exposes five Click commands: ``generate`` (NL description or YAML → ZIP of Terraform
files), ``plan`` (dry-run RBAC preview), ``explain`` (FlowGraph inspection),
``validate`` (run checkov/tfsec/infracost on an existing directory), ``portal``
(launch the FastAPI web UI), and ``doctor`` / ``import-adf`` for diagnostics and ADF
migration.  All generation commands follow the same pipeline: parse → RbacResolver →
HclGenerator/DataProductGenerator → OutputWriter, then optionally CheckovRunner.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows regardless of the active code page.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dataforge import __version__
from dataforge.config import Settings, get_settings
from dataforge.generation.data_product_generator import DataProductGenerator
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.llm.adapter import build_adapter
from dataforge.models.flow_graph import FlowMetadata
from dataforge.output.writer import OutputWriter
from dataforge.parsing.adf_importer import AdfImporter, AdfImportError
from dataforge.parsing.intent_parser import IntentParser, ParseError
from dataforge.parsing.intent_resolver import IntentResolver
from dataforge.parsing.yaml_parser import YamlParser
from dataforge.rbac.resolver import RbacResolver
from dataforge.validation.checkov_runner import CheckovRunner
from dataforge.validation.tfsec_runner import TfsecRunner
from dataforge.validation.infracost_runner import InfracostRunner

# legacy_windows=False forces the Python I/O path on Windows, avoiding the CP1252
# LegacyWindowsTerm fallback that mangles Rich colour codes and Unicode symbols.
console = Console(legacy_windows=False)


@click.group()
@click.version_option(__version__, prog_name="dataforge")
def cli() -> None:
    """DataForge — Intent-to-Infrastructure for Azure data engineering stacks.

    Converts natural-language data flow descriptions into production-ready
    Terraform (ADF / Databricks / Fabric / ADLS / Key Vault) with automatic
    RBAC role assignment wiring.
    """


@cli.command()
@click.argument("description", required=False, default=None)
@click.option("--from", "from_file", type=click.Path(path_type=Path), default=None,
              help="Path to a data-product.yaml file (alternative to DESCRIPTION)")
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
    description: str | None,
    from_file: Path | None,
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
    """Generate Terraform from a natural-language description or data-product.yaml.

    \b
    Examples:
        dataforge generate "Read Parquet from ADLS, transform in Databricks, write to Fabric Lakehouse"
        dataforge generate --from data-product.yaml
    """
    _configure_logging(verbose)

    if description and from_file:
        console.print("[red]Error:[/red] Provide either DESCRIPTION or --from, not both.")
        sys.exit(1)
    if not description and not from_file:
        console.print("[red]Error:[/red] Provide either a DESCRIPTION argument or --from <file>.")
        sys.exit(1)

    # ── YAML path ────────────────────────────────────────────────────────────
    # Explicit YAML skips the LLM parsing step entirely; the graph is built
    # deterministically from the data-product.yaml schema via IntentResolver.
    if from_file:
        try:
            product = YamlParser().parse_file(from_file)
        except ParseError as exc:
            console.print(f"[red]Parse failed:[/red] {exc}")
            sys.exit(1)

        try:
            graph = IntentResolver().resolve(product, env=env)
        except ValueError as exc:
            console.print(f"[red]Resolution failed:[/red] {exc}")
            sys.exit(1)

        console.print(
            Panel(
                f"[bold]DataProduct:[/bold] [cyan]{product.name}[/cyan] "
                f"({'intent' if product.is_intent_form else 'explicit'} form)\n"
                f"[bold]FlowGraph:[/bold] {len(graph.nodes)} nodes · {len(graph.edges)} edges\n"
                + "\n".join(f"  - {n.type.value}: [cyan]{n.name}[/cyan]" for n in graph.nodes),
                title=f"Loaded from {from_file.name}",
                border_style="blue",
            )
        )

        rbac = RbacResolver().resolve(graph)
        _print_rbac_plan(rbac)

        if dry_run:
            console.print("[yellow]Dry run — no files written.[/yellow]")
            return

        tf_result = HclGenerator(Renderer(), None).generate(graph, rbac, llm_polish=False)
        platform_result = DataProductGenerator().generate(product, graph, rbac)
        all_files = tf_result.files + platform_result.files
        all_warnings = tf_result.warnings + platform_result.warnings

        try:
            paths = OutputWriter().write(all_files, output, overwrite=overwrite)
        except FileExistsError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

        tf_count = len(tf_result.files)
        platform_count = len(platform_result.files)
        console.print(
            f"\n[green]OK[/green] Wrote {len(paths)} files to [cyan]{output}/[/cyan] "
            f"({tf_count} Terraform · {platform_count} platform):"
        )
        for p in paths:
            console.print(f"  {p.name}")

        if all_warnings:
            console.print("\n[yellow]Warnings:[/yellow]")
            for w in all_warnings:
                console.print(f"  [yellow]WARN[/yellow] {w}")

        if not no_validate:
            with console.status("[bold cyan]Running Checkov…"):
                report = CheckovRunner().run(output)
            _print_checkov_report(report)

        if json_output:
            out = {"files": [f.filename for f in all_files], "warnings": all_warnings}
            print(json.dumps(out, indent=2))

        return

    # ── NL path — requires an LLM provider ───────────────────────────────────
    # Natural-language input is sent to the configured LLM (IntentParser) to
    # extract a structured FlowGraph.  Enforce the length cap here, before
    # initialising the adapter, to avoid an unnecessary API-key round-trip.
    if len(description) > IntentParser.MAX_DESCRIPTION_LEN:
        console.print(
            f"[red]Error:[/red] Description too long "
            f"({len(description)} chars, max {IntentParser.MAX_DESCRIPTION_LEN})."
        )
        sys.exit(1)

    settings = get_settings()
    try:
        adapter = build_adapter(settings)
    except (ValueError, ImportError) as exc:
        console.print(f"[red]LLM configuration error:[/red] {exc}")
        sys.exit(1)

    metadata_overrides: dict = {
        "location": region,
        "resource_group": resource_group,
        "environment": env,
        "application_name": app_name,
        "tags": {"managed-by": "dataforge", "environment": env},
    }

    provider_label = settings.llm_provider
    with console.status(f"[bold cyan]Parsing description with {provider_label}…"):
        try:
            graph = IntentParser(adapter).parse(description, metadata_overrides)
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
        result = HclGenerator(Renderer(), None).generate(graph, rbac, llm_polish=False)
    else:
        with console.status(f"[bold cyan]Generating Terraform with {provider_label}…"):
            result = HclGenerator(Renderer(), adapter).generate(graph, rbac, llm_polish=True)

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
@click.option("--skip-tfsec",     is_flag=True, help="Skip tfsec security scan")
@click.option("--skip-infracost", is_flag=True, help="Skip infracost cost estimate")
def validate(directory: Path, skip_tfsec: bool, skip_infracost: bool) -> None:
    """Run Checkov, tfsec, and infracost on an existing Terraform directory.

    \b
    Requires:
        pip install checkov
        tfsec     — https://github.com/aquasecurity/tfsec
        infracost — https://www.infracost.io/docs

    \b
    Example:
        dataforge validate ./output
        dataforge validate ./output --skip-infracost
    """
    all_ok = True

    with console.status("[bold cyan]Running Checkov…"):
        checkov = CheckovRunner().run(directory)
    _print_checkov_report(checkov)
    if not checkov.ok:
        all_ok = False

    if not skip_tfsec:
        with console.status("[bold cyan]Running tfsec…"):
            tfsec = TfsecRunner().run(directory)
        _print_tfsec_report(tfsec)
        if tfsec.installed and not tfsec.ok:
            all_ok = False

    if not skip_infracost:
        with console.status("[bold cyan]Running infracost…"):
            cost = InfracostRunner().run(directory)
        _print_infracost_report(cost)

    sys.exit(0 if all_ok else 1)


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
    if len(description) > IntentParser.MAX_DESCRIPTION_LEN:
        console.print(
            f"[red]Error:[/red] Description too long "
            f"({len(description)} chars, max {IntentParser.MAX_DESCRIPTION_LEN})."
        )
        sys.exit(1)

    settings = get_settings()
    try:
        adapter = build_adapter(settings)
    except (ValueError, ImportError) as exc:
        console.print(f"[red]LLM configuration error:[/red] {exc}")
        sys.exit(1)

    overrides = {"location": region, "environment": env, "resource_group": resource_group}

    with console.status("[bold cyan]Parsing description…"):
        try:
            graph = IntentParser(adapter).parse(description, overrides)
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
    # The comparison is role-name–only; scopes and principal IDs differ between
    # the planned Terraform expressions and the live Azure resource IDs, so a
    # perfect semantic diff is not possible without `terraform plan` output.
    if compare_azure:
        _compare_with_azure(rbac, resource_group, subscription_id)


@cli.command()
@click.argument("description")
@click.option("--region", default="eastus", show_default=True)
@click.option("--env", default="dev", show_default=True)
def explain(description: str, region: str, env: str) -> None:
    """Parse a description and show the FlowGraph + RBAC plan without writing files."""
    if len(description) > IntentParser.MAX_DESCRIPTION_LEN:
        console.print(
            f"[red]Error:[/red] Description too long "
            f"({len(description)} chars, max {IntentParser.MAX_DESCRIPTION_LEN})."
        )
        sys.exit(1)

    settings = get_settings()
    try:
        adapter = build_adapter(settings)
    except (ValueError, ImportError) as exc:
        console.print(f"[red]LLM configuration error:[/red] {exc}")
        sys.exit(1)

    overrides = {"location": region, "environment": env}

    with console.status("[bold cyan]Parsing…"):
        try:
            graph = IntentParser(adapter).parse(description, overrides)
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


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes (dev mode)")
def portal(host: str, port: int, reload: bool) -> None:
    """Launch the DataForge self-service web portal.

    Opens a local web interface where data engineers can fill a form and
    download a complete production-ready Terraform stack as a ZIP file.

    \b
    Example:
        dataforge portal
        dataforge portal --port 8080
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Portal dependencies not installed.[/red] Run:\n"
            "  pip install 'dataforge[portal]'"
        )
        sys.exit(1)

    console.print(
        Panel(
            f"[bold cyan]DataForge Portal[/bold cyan]\n"
            f"Open [link=http://{host}:{port}]http://{host}:{port}[/link] in your browser",
            border_style="cyan",
        )
    )
    uvicorn.run(
        "dataforge.portal.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@cli.command()
def doctor() -> None:
    """Check that all DataForge dependencies are correctly installed and configured.

    Runs a series of health checks and reports what is ready vs. missing.
    Run this first when setting up a new environment.

    \b
    Checks:
        terraform   — Terraform CLI
        ansible     — Ansible (for Alfresco playbooks)
        checkov     — Checkov IaC scanner
        tfsec       — tfsec security scanner
        infracost   — infracost cost estimator
        API keys    — DATAFORGE_* LLM provider key
        azure-cli   — az CLI + active login
    """
    import shutil

    results: list[tuple[str, bool, str]] = []

    def _check(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        icon = "[green]OK[/green]" if ok else "[red]MISSING[/red]"
        suffix = f" [dim]{detail}[/dim]" if detail else ""
        console.print(f"  {icon}  {label}{suffix}")

    console.print(Panel("[bold]DataForge Doctor[/bold] — environment health check", border_style="cyan"))

    # ── CLI tools ─────────────────────────────────────────────────────────────
    console.print("\n[bold]CLI Tools[/bold]")

    tf = shutil.which("terraform")
    if tf:
        try:
            proc = subprocess.run(["terraform", "version", "-json"], capture_output=True, text=True, timeout=10)
            tf_ver = json.loads(proc.stdout).get("terraform_version", "?") if proc.returncode == 0 else "?"
            _check("terraform", True, tf_ver)
        except Exception:
            _check("terraform", True, "(version unknown)")
    else:
        _check("terraform", False, "install: https://developer.hashicorp.com/terraform/install")

    ansible = shutil.which("ansible")
    if ansible:
        try:
            proc = subprocess.run(["ansible", "--version"], capture_output=True, text=True, timeout=10)
            first_line = proc.stdout.splitlines()[0] if proc.stdout else "ansible"
            _check("ansible", True, first_line.strip())
        except Exception:
            _check("ansible", True, "(version unknown)")
    else:
        _check("ansible", False, "install: pip install ansible")

    checkov_ok = bool(shutil.which("checkov"))
    if not checkov_ok:
        try:
            proc = subprocess.run([sys.executable, "-m", "checkov", "--version"], capture_output=True, text=True, timeout=10)
            checkov_ok = proc.returncode == 0
        except Exception:
            pass
    _check("checkov", checkov_ok,
           "" if checkov_ok else "install: pip install checkov")

    tfsec_ok = bool(shutil.which("tfsec"))
    _check("tfsec", tfsec_ok,
           "" if tfsec_ok else "install: https://github.com/aquasecurity/tfsec#installation")

    infracost_ok = bool(shutil.which("infracost"))
    _check("infracost", infracost_ok,
           "" if infracost_ok else "install: https://www.infracost.io/docs")

    # ── API keys ──────────────────────────────────────────────────────────────
    console.print("\n[bold]API Keys[/bold]")
    import os

    settings = get_settings()
    provider = settings.llm_provider
    key_var_map = {
        "anthropic": "DATAFORGE_ANTHROPIC_API_KEY",
        "openai":    "DATAFORGE_OPENAI_API_KEY",
        "groq":      "DATAFORGE_OPENAI_API_KEY",
        "ollama":    None,
        "mistral":   "DATAFORGE_OPENAI_API_KEY",
        "azure_openai": "DATAFORGE_OPENAI_API_KEY",
    }
    key_var = key_var_map.get(provider)
    if key_var is None:
        _check(f"LLM key ({provider})", True, "Ollama runs locally — no key required")
    elif os.environ.get(key_var) or getattr(settings, key_var.lower().replace("dataforge_", ""), None):
        _check(f"LLM key ({provider})", True, f"${key_var} is set")
    else:
        _check(f"LLM key ({provider})", False, f"export {key_var}=<your-api-key>")

    # ── Azure CLI ─────────────────────────────────────────────────────────────
    console.print("\n[bold]Azure[/bold]")
    az_ok = bool(shutil.which("az"))
    if az_ok:
        try:
            proc = subprocess.run(
                ["az", "account", "show", "--output", "json"],
                capture_output=True, text=True, timeout=15
            )
            if proc.returncode == 0:
                acct = json.loads(proc.stdout)
                _check("azure-cli", True,
                       f"logged in as {acct.get('user', {}).get('name', '?')} "
                       f"({acct.get('name', '?')})")
            else:
                _check("azure-cli", False, "run: az login")
        except Exception:
            _check("azure-cli", True, "(version unknown)")
    else:
        _check("azure-cli", False, "install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli")

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)
    failed = total - passed

    console.print(
        f"\n[bold]Summary:[/bold] {passed}/{total} checks passed"
        + (f" · [red]{failed} missing[/red]" if failed else " · [green]all clear[/green]")
    )
    if failed:
        console.print("[dim]Fix the items above, then re-run: dataforge doctor[/dim]")

    sys.exit(0 if failed == 0 else 1)


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
        result = HclGenerator(Renderer(), None).generate(graph, rbac, llm_polish=False)

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


def _print_tfsec_report(report) -> None:  # type: ignore[no-untyped-def]
    if not report.installed:
        console.print("\ntfsec: [yellow]SKIPPED[/yellow] (not installed — run: install tfsec)")
        return
    status = "[green]PASS[/green]" if report.ok else "[red]FAIL[/red]"
    console.print(
        f"\ntfsec: {status} | "
        f"[red]{report.critical} critical[/red] / "
        f"[yellow]{report.high} high[/yellow] / "
        f"{report.medium} medium / {report.low} low"
    )
    shown = report.findings[:10]
    for f in shown:
        sev_col = "red" if f.severity in ("CRITICAL", "HIGH") else "yellow"
        console.print(
            f"  [{sev_col}]{f.severity}[/{sev_col}] {f.rule_id} — {f.description[:80]}"
            + (f" ({f.filename}:{f.start_line})" if f.filename else "")
        )
    if len(report.findings) > 10:
        console.print(f"  … and {len(report.findings) - 10} more")


def _print_infracost_report(report) -> None:  # type: ignore[no-untyped-def]
    if not report.installed:
        console.print("\ninfracost: [yellow]SKIPPED[/yellow] (not installed — run: install infracost)")
        return
    if report.error:
        console.print(f"\ninfracost: [yellow]WARN[/yellow] Could not estimate cost: {report.error}")
        return
    console.print(
        f"\ninfracost: [cyan]${report.total_monthly_cost:.2f}/mo[/cyan] "
        f"([dim]{report.currency}[/dim])"
    )
    for r in report.resources[:8]:
        if r.monthly_cost > 0:
            console.print(f"  {r.name}: [cyan]${r.monthly_cost:.2f}/mo[/cyan]")
    if len(report.resources) > 8:
        console.print(f"  … and {len(report.resources) - 8} more resources")


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

    # Index by display name only — `az role assignment list` returns the display
    # name in roleDefinitionName, which aligns with the role_name strings used
    # throughout the RBAC matrix and RoleAssignment model.
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
