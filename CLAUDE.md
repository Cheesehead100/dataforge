# DataForge — Claude Context

## What This Is

DataForge converts natural-language data pipeline descriptions into production-ready
Terraform HCL for Azure data engineering stacks. Core targets: ADF, Databricks, Fabric,
ADLS Gen2, Key Vault, SQL MI.

**Key innovation**: The RBAC matrix in `src/dataforge/rbac/matrix.py` — a deterministic
lookup table that derives every required Azure role assignment from the data flow graph.
This file must NEVER be replaced by LLM output. Tests in `tests/unit/test_rbac_matrix.py`
assert every entry.

## Architecture

```
NL description
  → IntentParser (Claude Haiku)
    → FlowGraph (Pydantic model)
      → RbacResolver (deterministic, no AI)
        → RbacResult
          → HclGenerator (Jinja2 skeleton + optional Sonnet polish)
            → TerraformFile list
              → OutputWriter → ./output/*.tf
```

## Critical Rules

1. **rbac.tf is never LLM-polished** — `HclGenerator._SKIP_LLM_FILES` enforces this.
2. **RBAC matrix is the source of truth** — do not add roles to templates; add them to `matrix.py`.
3. **FlowGraph is immutable** — `frozen=True` on FlowNode and FlowEdge.
4. **IntentParser uses tool-use (not free text)** — the FlowGraph JSON schema is passed as the tool definition, forcing structured output.

## Running Locally

```bash
pip install -e ".[dev]"
cp .env.example .env        # add your ANTHROPIC_API_KEY

# Generate Terraform (with LLM)
dataforge generate "Read Parquet from ADLS, transform in Databricks, write to Fabric Lakehouse"

# Skeleton only (no API call after parsing)
dataforge generate "..." --no-llm-polish

# Dry run: see FlowGraph + RBAC plan, write nothing
dataforge explain "..."

# Run tests
pytest
```

## Agent Routing

| Task | Agent |
|------|-------|
| Add a new node type or operation to RBAC matrix | Edit `rbac/matrix.py` + `rbac/roles.py`; run `test_rbac_matrix.py` first (TDD) |
| Add a new Terraform template | Add `.j2` file to `generation/templates/`, register in `renderer.py:NODE_TYPE_TEMPLATE` |
| Debug parse failures | Check `parsing/prompts.py` system prompt; increase `MAX_RETRIES` in intent_parser |
| New CLI flag | Add to `cli.py:generate` click options; pass through to HclGenerator |

## Test Coverage Targets

- `rbac/` package: 100% (it is the core IP — no LLM, pure Python)
- Overall: ≥70% (`pyproject.toml` enforces this; target 80% at Phase 2)

## Model Usage

- **Claude Haiku 4.5** — intent parsing (cheap, fast, tool-use)
- **Claude Sonnet 4.6** — HCL polish pass (higher quality output)
- Neither model is called for RBAC resolution — it is deterministic Python.
