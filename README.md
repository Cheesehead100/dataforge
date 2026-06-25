# DataForge

**Turn a data product YAML into a production-ready Azure data platform — automatically.**

DataForge generates the complete platform stack from a single declarative configuration: Terraform, Unity Catalog governance, PySpark data quality checks, CI/CD pipelines, Azure Monitor alerts, Ansible post-provisioning playbooks, ADF pipeline resources, drift detection, SRE dashboards, cost optimization, private endpoint networking, and a readiness validation suite that blocks promotion until the platform is actually working.

```yaml
# data-product.yaml
product: customer360
environment: prod
source:
  type: sqlserver
target:
  type: fabric
sla: hourly
classification:
  pii: true
```

```bash
dataforge generate --from data-product.yaml
```

```
✓ Wrote 30 files to ./output/ (12 Terraform · 18 platform)

  # Terraform infrastructure
  providers.tf · variables.tf · storage.tf · databricks.tf
  data_factory.tf · adf_pipeline.tf · key_vault.tf · rbac.tf
  sql_mi.tf · monitoring.tf · networking.tf · outputs.tf

  # CI/CD (GitHub Actions + Azure DevOps)
  .github/workflows/dataforge-deploy.yml
  .github/workflows/dataforge-drift.yml
  .github/workflows/dataforge-cost-optimization.yml
  azure-pipelines.yml

  # Ansible (post-provisioning configuration)
  ansible/inventory.yml
  ansible/playbooks/configure_databricks.yml
  ansible/requirements.yml

  # Readiness gate (blocks promotion until platform is verified)
  tests/readiness/conftest.py · test_storage.py · test_platform.py
  tests/readiness/requirements.txt · run_readiness.sh

  # SRE operations
  sre/workbook.tf · sre/workbook.json · sre/runbook.md

  # Cost + drift automation
  scripts/analyze_costs.py · scripts/drift_notify.py
```

Add `networking.private_endpoints: true` and DataForge generates 3 additional files:
`network/private_endpoints.tf`, `network/dns.tf`, `network/sequencing.sh` — solving the
Azure private endpoint deployment ordering problem automatically.

---

## The Problem

Enterprise data teams hit the same eight walls on every deployment:

| Pain Point | What breaks |
|---|---|
| **Terraform ≠ working platform** | `apply` succeeds but users get 403s — RBAC, Unity Catalog grants, and secret scopes are missing |
| **Private endpoint hell** | Storage containers fail mid-plan because firewall activates before DNS propagates |
| **Terraform drift** | UI changes accumulate silently; months later `plan` shows 842 changes |
| **Data engineers become cloud engineers** | Hired to build pipelines, now writing VNet injection HCL |
| **Dev works, prod fails** | Hardcoded catalog and storage names break on promotion |
| **Production data quality issues** | Pipeline deploys successfully but produces duplicates, nulls, schema drift |
| **Nobody owns production operations** | Platform → Data → Cloud blame triangle; no single SRE layer |
| **Cost explosions** | Clusters left running at 10% utilisation; no policy enforcement at deploy time |

DataForge eliminates all eight by generating the entire platform stack — infrastructure, governance, quality, CI/CD, monitoring, configuration, validation, and operations — from a single YAML file.

---

## What Gets Generated

A single `dataforge generate --from data-product.yaml` produces:

| Output | Generator | What it solves |
|---|---|---|
| `*.tf` — all Azure resources | TerraformGenerator | Infrastructure + RBAC (Pain Points 2, 4) |
| `adf_pipeline.tf` — linked services, datasets, Copy activity, schedule trigger | AdfPipelineGenerator | sqlserver/eventhub → ADF → ADLS actually moves data |
| `governance.tf` — Unity Catalog catalog, schemas, grants | GovernanceGenerator | Databricks access without manual UC bootstrap (Pain Point 1) |
| `quality/*.py` + `quality/databricks_jobs.tf` | QualityGenerator | PySpark validation jobs scheduled in Databricks (Pain Point 6) |
| `.github/workflows/` or `azure-pipelines-deploy.yml` | CiCdGenerator | Security gates: checkov, tfsec, infracost, per-env promotion (Pain Point 5) |
| `monitoring.tf` — metric alerts, action groups, cost budgets | MonitoringGenerator | Alert on failure + block spend overruns (Pain Points 7, 8) |
| `ansible/` — REST API playbooks for Databricks config | AnsibleGenerator | Cluster policy, secret scopes, Unity Catalog bootstrap (Pain Point 1) |
| `tests/readiness/` — pytest suite + shell runner | ReadinessGenerator | Blocks promotion until storage, catalog, secrets, and DNS are verified (Pain Point 1) |
| `.github/workflows/dataforge-drift.yml` + `scripts/drift_notify.py` | DriftDetectionGenerator | Nightly scheduled `terraform plan`; alerts on state drift (Pain Point 3) |
| `sre/workbook.tf` + `sre/workbook.json` + `sre/runbook.md` | SreDashboardGenerator | Azure Monitor Workbook + per-product runbook (Pain Point 7) |
| `scripts/analyze_costs.py` + `dataforge-cost-optimization.yml` | CostOptimizationGenerator | Weekly rightsizing engine — flags idle clusters, opens GH issues (Pain Point 8) |
| `network/private_endpoints.tf` + `network/dns.tf` + `network/sequencing.sh` | NetworkingGenerator | Explicit PE dependency chains + 6-stage deploy script (Pain Point 2) |

---

## Architecture

```
Data Product YAML
      │
      ▼
DataForge Engine
  ├── YamlParser              — validates schema, two forms: intent or explicit
  ├── IntentResolver          — deterministic: source+target → FlowGraph (no LLM)
  └── RbacResolver            — deterministic: graph edges → role assignments
      │
      ▼
Generator Registry
  ├── TerraformGenerator      → *.tf + rbac.tf
  ├── AdfPipelineGenerator    → adf_pipeline.tf
  ├── GovernanceGenerator     → governance.tf
  ├── QualityGenerator        → quality/*.py + databricks_jobs.tf
  ├── CiCdGenerator           → .github/workflows/ or azure-pipelines.yml
  ├── MonitoringGenerator     → monitoring.tf
  ├── AnsibleGenerator        → ansible/playbooks/
  ├── ReadinessGenerator      → tests/readiness/
  ├── DriftDetectionGenerator → .github/workflows/drift.yml + scripts/drift_notify.py
  ├── SreDashboardGenerator   → sre/workbook.tf + sre/workbook.json + sre/runbook.md
  ├── CostOptimizationGenerator → scripts/analyze_costs.py + CI/CD weekly job
  └── NetworkingGenerator     → network/private_endpoints.tf + dns.tf + sequencing.sh
      │
      ▼
Output files
```

**RBAC and Unity Catalog grants are always deterministic** — built from a lookup matrix, never generated by an LLM. The only AI-touched layer is natural-language intent parsing (the `dataforge generate "..."` path). The `--from` YAML path uses zero AI.

---

## Quickstart

**Requirements:** Python 3.11+, optional LLM API key (only needed for NL input)

```bash
git clone https://github.com/Cheesehead100/dataforge
cd dataforge
pip install -e ".[dev]"
```

### YAML input (recommended — no API key needed)

```bash
# Generate full platform stack from a data product YAML
dataforge generate --from data-product.yaml

# Target a specific environment
dataforge generate --from data-product.yaml --env prod

# Preview FlowGraph + RBAC plan without writing files
dataforge generate --from data-product.yaml --dry-run

# Write to a specific directory
dataforge generate --from data-product.yaml -o ./infra/customer360
```

### Natural language input (any LLM provider)

```bash
cp .env.example .env
# edit .env — choose your provider:

# Option 1: Anthropic (default)
DATAFORGE_ANTHROPIC_API_KEY=sk-ant-...

# Option 2: OpenAI or any compatible endpoint
pip install 'dataforge[openai]'
DATAFORGE_LLM_PROVIDER=openai
DATAFORGE_OPENAI_API_KEY=sk-...

# Option 3: Groq (fast inference, free tier available)
DATAFORGE_LLM_PROVIDER=groq
DATAFORGE_OPENAI_API_KEY=gsk_...

# Option 4: Ollama (local, no API key needed)
DATAFORGE_LLM_PROVIDER=ollama
DATAFORGE_OPENAI_BASE_URL=http://localhost:11434/v1
DATAFORGE_OPENAI_PARSE_MODEL=llama3
```

```bash
dataforge generate "ADF reads from SQL MI, transforms in Databricks, writes to Fabric Lakehouse"
dataforge generate "..." --no-llm-polish   # skeleton only, no AI polish pass
dataforge explain  "..."                   # show FlowGraph + RBAC plan, write nothing
```

### Self-service portal

A form-based web UI for data engineers who prefer not to write YAML:

```bash
pip install 'dataforge[portal]'
dataforge portal            # opens at http://localhost:8000
dataforge portal --port 8080 --reload
```

Fill in the form → click **Preview** to see the FlowGraph and generated YAML → click **Download** to get a ZIP of the full Terraform stack.

### After generation

```bash
# 1. Apply Terraform
cd output/
terraform init && terraform apply

# 2. Configure Databricks (runs after terraform apply)
export DATABRICKS_HOST=$(terraform output -raw databricks_workspace_url)
export DATABRICKS_TOKEN=<your PAT>
ansible-galaxy collection install -r ansible/requirements.yml
ansible-playbook -i ansible/inventory.yml ansible/playbooks/configure_databricks.yml

# 3. Run readiness gate (blocks promotion if anything is wrong)
bash tests/readiness/run_readiness.sh

# 4. Private endpoints — use the generated sequencing script if networking.private_endpoints: true
bash network/sequencing.sh   # 6-stage deploy that respects Azure DNS propagation
```

---

## Data Product YAML

Two forms are supported. Both produce identical output.

### Intent form — recommended starting point

```yaml
product: customer360
environment: dev

source:
  type: sqlserver      # sqlserver | eventhub | adls | blob

target:
  type: fabric         # fabric | adls

sla: hourly            # hourly | daily | weekly

classification:
  pii: true

retention:
  bronze: 90
  silver: 365
  gold: 2555
```

DataForge resolves `sqlserver → fabric` to the standard pipeline:
`SQL MI → ADF → ADLS bronze → Databricks → ADLS silver/gold → Fabric Lakehouse`

### Explicit form — full control

```yaml
apiVersion: dataforge/v1
kind: DataProduct

metadata:
  name: customer-analytics
  owner: data-engineering@company.com
  domain: marketing
  sensitivity: confidential
  sla:
    freshness: 4h
    availability: 99.9%

environments:
  dev:
    subscription_id: 00000000-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-customer-analytics-dev
  prod:
    subscription_id: 00000000-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-customer-analytics-prod

pipeline:
  nodes:
    - { id: source_sql, type: sql_mi,          name: "CRM DB" }
    - { id: ingest,     type: adf,             name: "ADF" }
    - { id: bronze,     type: adls,            name: "Bronze Lake" }
    - { id: transform,  type: databricks,      name: "Databricks" }
    - { id: secrets,    type: key_vault,       name: "Key Vault" }
    - { id: warehouse,  type: fabric_lakehouse, name: "Fabric" }
  edges:
    - { from: source_sql, to: ingest,     operation: read }
    - { from: ingest,     to: bronze,     operation: write }
    - { from: ingest,     to: transform,  operation: trigger }
    - { from: transform,  to: bronze,     operation: read }
    - { from: transform,  to: secrets,    operation: secret_get }
    - { from: transform,  to: warehouse,  operation: write }

governance:
  unity_catalog:
    metastore: unity-catalog-prod
    catalog: customer_analytics
    schemas: [bronze, silver, gold]
    grants:
      - principal: data-engineers@company.com
        privileges: [USE_CATALOG, USE_SCHEMA, SELECT, MODIFY]
        on: catalog

quality:
  checks:
    - layer: silver
      table: customer_events
      rules:
        - not_null: [customer_id, event_timestamp]
        - unique: [event_id]
        - freshness_within: { column: event_timestamp, hours: 6 }

cicd:
  provider: github_actions     # github_actions | azure_devops
  gates: [terraform_format, checkov_scan, tfsec_scan, python_unit_tests, cost_estimate]
  environments:
    - { name: dev,  auto_deploy: true }
    - { name: prod, approval_required: true, smoke_test: true }

monitoring:
  alerts:
    - { name: pipeline_failure, metric: adf_pipeline_run_failed, threshold: 1, severity: critical, channel: "email:ops@company.com" }
    - { name: freshness_breach, metric: data_freshness_hours,    threshold: 6, severity: warning,  channel: "email:ops@company.com" }
  cost:
    monthly_budget_usd: 1000
    alert_at_pct: [75, 90, 100]
    alert_channel: "email:finops@company.com"

networking:
  vnet_cidr: 10.20.0.0/16
  private_endpoints: true
  databricks_vnet_injection: true

compute:
  databricks:
    node_type: Standard_DS3_v2
    autoscale: { min_workers: 2, max_workers: 8 }
    spot_enabled: true
```

See [`data-product.example.yaml`](data-product.example.yaml) and [`docs/NORTH_STAR.md`](docs/NORTH_STAR.md) for the full schema reference.

---

## Supported Resource Types

| Node Type | Azure Resource | As Principal | As Scope |
|---|---|:---:|:---:|
| `adf` | Azure Data Factory | ✓ | ✓ |
| `databricks` | Azure Databricks Workspace (VNet-injected) | ✓ | ✓ |
| `aks` | AKS + UAMI + OIDC federated credential | ✓ | — |
| `fabric_lakehouse` | Microsoft Fabric Lakehouse | ✓ | ✓ |
| `adls` | Azure Data Lake Storage Gen2 (HNS) | — | ✓ |
| `key_vault` | Azure Key Vault | — | ✓ |
| `sql_mi` | Azure SQL Managed Instance | ✓ | ✓ |
| `eventhub` | Azure Event Hub | — | ✓ |
| `blob_storage` | Azure Blob Storage | — | ✓ |

---

## CLI Reference

```
dataforge generate [DESCRIPTION] [OPTIONS]

  DESCRIPTION         Natural-language pipeline description (quoted string)
                      Required if --from is not set; not allowed if --from is set

Options:
  --from PATH             Data product YAML file (no API key required)
  -o, --output PATH       Output directory [default: ./output]
  --env [dev|test|prod]   Environment override [default: dev]
  --region TEXT           Azure region [default: eastus]
  --resource-group TEXT   Resource group name
  --app-name TEXT         Application name for resource naming
  --no-validate           Skip Checkov validation
  --no-llm-polish         Skeleton-only; no LLM polish pass (NL path only)
  --overwrite             Overwrite existing output directory
  --dry-run               Print FlowGraph + RBAC plan, write nothing
  --json-output           Emit machine-readable JSON to stdout
  -v, --verbose           Increase verbosity

dataforge explain DESCRIPTION      Parse NL input, show FlowGraph + RBAC plan, no files
dataforge validate DIRECTORY       Run Checkov on an existing Terraform directory
dataforge portal [OPTIONS]         Launch the self-service web portal
  --host TEXT                        Bind host [default: 0.0.0.0]
  --port INTEGER                     Port [default: 8000]
  --reload                           Auto-reload on code changes (dev mode)
dataforge --version
```

### LLM provider environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATAFORGE_LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `groq` \| `ollama` \| `mistral` \| `together` \| `azure_openai` |
| `DATAFORGE_ANTHROPIC_API_KEY` | — | Required when provider is `anthropic` |
| `DATAFORGE_OPENAI_API_KEY` | — | Required for all OpenAI-compatible providers |
| `DATAFORGE_OPENAI_BASE_URL` | provider default | Custom endpoint — use for Ollama, Azure OpenAI, self-hosted |
| `DATAFORGE_OPENAI_PARSE_MODEL` | `gpt-4o` | Model for structured JSON extraction (NL → FlowGraph) |
| `DATAFORGE_OPENAI_GENERATE_MODEL` | `gpt-4o` | Model for HCL polish pass |

---

## Security Defaults

All generated Terraform enforces security-first defaults:

- `public_network_access_enabled = false` on storage and ADF
- `enable_rbac_authorization = true` on all Key Vaults (no legacy access policies)
- `infrastructure_encryption_enabled = true` on ADLS (double encryption at rest)
- `no_public_ip = true` on Databricks (Secure Cluster Connectivity)
- `purge_protection_enabled = true` on Key Vaults
- `oidc_issuer_enabled = true` + `workload_identity_enabled = true` on AKS
- All CI/CD pipelines run `checkov` and `tfsec` as blocking gates before deploy
- Storage containers asserted non-public by the readiness test suite

---

## Running Tests

```bash
pytest                          # all tests (≥80% coverage enforced)
pytest tests/unit/              # unit tests only (no API calls, no Azure)
pytest -k "readiness"           # readiness generator tests
pytest -k "adf"                 # ADF pipeline generator tests
pytest -k "networking"          # private endpoint / DNS generator tests
pytest -k "portal"              # self-service portal tests
```

Current suite: **473 tests, 8 skipped, 85% coverage**.

---

## Roadmap

| Loop | Status | Delivers |
|---|---|---|
| L1 | ✅ Shipped | NL → Terraform + RBAC (deterministic) |
| L2 | ✅ Shipped | YAML input path + intent resolver (no API key) |
| L3 | ✅ Shipped | Unity Catalog governance (`governance.tf`) |
| L4 | ✅ Shipped | PySpark data quality scripts + `databricks_jobs.tf` |
| L5 | ✅ Shipped | CI/CD pipelines (GitHub Actions + Azure DevOps) with 7 security gates |
| L6 | ✅ Shipped | Azure Monitor alerts + cost budgets (`monitoring.tf`) |
| L7 | ✅ Shipped | Ansible playbooks (Databricks REST API — cluster policy, secret scopes, Unity Catalog) |
| L8 | ✅ Shipped | Readiness validation suite + nightly drift detection |
| ADF | ✅ Shipped | ADF linked services, datasets, Copy pipeline, schedule trigger (`adf_pipeline.tf`) |
| L9 | ✅ Shipped | Cost optimization engine (weekly rightsizing) + SRE dashboard + per-product runbooks |
| L10 | ✅ Shipped | Private endpoint networking generator + self-service web portal + multi-LLM support |

---

## North Star

See [`docs/NORTH_STAR.md`](docs/NORTH_STAR.md) for the full vision: 8 enterprise pain points, generator registry design, platform architecture, phased roadmap, and success criteria.

> *A data engineer writes a YAML file and gets a production-ready, working data platform — without understanding Terraform, networking, or cloud infrastructure internals.*

---

## Contributing

1. **New RBAC rule** — edit `src/dataforge/rbac/matrix.py`, write the test in `tests/unit/test_rbac_matrix.py` first (TDD required).
2. **New generator** — subclass `BaseGenerator` in `src/dataforge/generation/generators/`, add a Jinja2 template, register in `DataProductGenerator`.
3. **New node type** — add to `constants.py`, add a template in `generation/templates/`, register in `rbac/matrix.py`.
4. **New LLM provider** — add a case to `build_adapter()` in `src/dataforge/llm/adapter.py`; extend `OpenAiAdapter` for OpenAI-compatible endpoints or implement `LlmAdapter` for a custom SDK.
5. All PRs must pass `pytest` with ≥80% coverage and a clean `checkov` run on generated output.

---

## License

MIT
