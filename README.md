# DataForge

**Intent-to-Infrastructure for Azure data engineering stacks.**

Describe your data pipeline in plain English. DataForge generates production-ready Terraform — including every RBAC role assignment — for ADF, Databricks, Microsoft Fabric, ADLS Gen2, Key Vault, and SQL MI.

```bash
dataforge generate "Read Parquet from ADLS, transform in Databricks, write to Fabric Lakehouse"
```

```
✓ Wrote 6 files to ./output/
  providers.tf
  variables.tf
  data_factory.tf
  databricks.tf
  fabric.tf
  storage.tf
  rbac.tf          ← all 7 role assignments, auto-wired
  outputs.tf
```

---

## The Problem

Getting RBAC right for Azure data pipelines is the #1 source of friction in IaC reviews:

- ADF managed identity needs **Storage Blob Data Reader** on ADLS
- ADF managed identity needs **Contributor** on Databricks to trigger jobs
- Databricks MSI needs **Storage Blob Data Contributor** on Fabric OneLake
- Key Vault secrets need **Key Vault Secrets User** on every caller

That's 7+ role assignments for a 3-node pipeline. Nobody gets them all right on the first try.

DataForge solves this with a **deterministic RBAC matrix** — a lookup table built from first principles, not guessed by an LLM.

---

## Architecture

```
Natural language
   │
   ▼ Claude Haiku (structured tool-use)
FlowGraph  ←── Pydantic model: nodes, edges, metadata
   │
   ├──▶ RbacResolver  ◀── RBAC matrix (pure Python, no AI)
   │         │
   │         ▼
   │    RbacResult    ──▶ rbac.tf   (deterministic, never LLM-polished)
   │
   ▼ Claude Sonnet (optional polish)
Jinja2 templates  ──▶ *.tf files
   │
   ▼ Checkov
Security report
```

---

## Quickstart

**Requirements:** Python 3.12+, an Anthropic API key

```bash
git clone https://github.com/Cheesehead100/dataforge
cd dataforge
pip install -e ".[dev]"

cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=sk-ant-...

# Full generation (parses + generates + validates)
dataforge generate "ADF reads Parquet from ADLS, triggers Databricks, writes to Fabric"

# Skip Sonnet polish (deterministic skeleton only — no cost after parsing)
dataforge generate "..." --no-llm-polish

# Dry run: see FlowGraph + RBAC plan without writing files
dataforge explain "ADF reads from ADLS and writes to Fabric Lakehouse"

# Validate existing Terraform directory
dataforge validate ./my-terraform-dir
```

---

## Supported Resource Types

| Node Type | Azure Resource | As Principal | As Scope |
|-----------|---------------|:---:|:---:|
| `adf` | Azure Data Factory | ✓ | ✓ |
| `databricks` | Azure Databricks Workspace | ✓ | ✓ |
| `fabric_lakehouse` | Microsoft Fabric Lakehouse | ✓ | ✓ |
| `adls` | Azure Data Lake Storage Gen2 | — | ✓ |
| `key_vault` | Azure Key Vault | — | ✓ |
| `sql_mi` | Azure SQL Managed Instance | ✓ | ✓ |
| `eventhub` | Azure Event Hub | — | ✓ |
| `blob_storage` | Azure Blob Storage | — | ✓ |

## Supported Operations (Edge Types)

| Operation | Meaning |
|-----------|---------|
| `read` | Source reads data from target |
| `write` | Source writes data to target |
| `trigger` | Source orchestrates/triggers target (ADF → Databricks) |
| `secret_get` | Source reads a secret from Key Vault |
| `connect` | Data-plane DB connection (SQL MI) |
| `stream` | Real-time ingest from Event Hub |

---

## CLI Reference

```
dataforge generate DESCRIPTION [OPTIONS]

  DESCRIPTION    Natural-language pipeline description (quoted string)

Options:
  -o, --output PATH       Output directory [default: ./output]
  --region TEXT           Azure region [default: eastus]
  --resource-group TEXT   Resource group name [default: rg-dataforge]
  --env [dev|test|prod]   Environment [default: dev]
  --app-name TEXT         Application name for resource naming [default: dataforge]
  --no-validate           Skip Checkov validation
  --no-llm-polish         Skeleton-only; no Sonnet polish pass
  --overwrite             Overwrite existing output directory
  --dry-run               Print FlowGraph + RBAC plan, write nothing
  --json-output           Emit machine-readable JSON to stdout
  -v, --verbose           Increase verbosity

dataforge explain DESCRIPTION    Parse and show FlowGraph + RBAC plan, no files
dataforge validate DIRECTORY     Run Checkov on an existing Terraform directory
dataforge --version
```

---

## Running Tests

```bash
pytest                          # all tests (coverage ≥70% enforced)
pytest tests/unit/              # unit tests only (no API calls)
pytest tests/unit/test_rbac_matrix.py -v  # RBAC matrix — every entry asserted
```

---

## Security Defaults

All generated Terraform follows security-first defaults:

- `public_network_access_enabled = false` on all storage and ADF
- `enable_rbac_authorization = true` on all Key Vaults (no legacy access policies)
- `infrastructure_encryption_enabled = true` on ADLS (double encryption)
- `no_public_ip = true` on Databricks custom parameters
- `purge_protection_enabled = true` on Key Vaults
- Private endpoints scaffolded (subnet IDs marked as TODO for your VNet)

---

## Roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| **Phase 1** | 🚧 In progress | ADF + Databricks + Fabric + ADLS — NL → Terraform + RBAC |
| Phase 2 | Planned | AKS Spark node pools, Helm chart generation, Ansible config |
| Phase 3 | Planned | ADF pipeline JSON import, Databricks job JSON import |
| Phase 4 | Planned | CI/CD pipeline generation (GitHub Actions / ADO) |

---

## Contributing

1. Add a new RBAC rule: edit `src/dataforge/rbac/matrix.py`, write a test in `tests/unit/test_rbac_matrix.py` first (TDD).
2. Add a new node type: add to `constants.py`, add template in `generation/templates/`, register in `renderer.py`.
3. All PRs must pass `pytest` with ≥70% coverage.

---

## License

MIT
