# DataForge North Star: Enterprise Data Product Platform

## Success Metric

> A data engineer requests a new production-ready data platform using a single declarative configuration — without understanding Terraform, networking, or cloud infrastructure internals.

---

## Problem Space: Why DataForge Exists

These are the eight recurring pain points DataForge is built to eliminate. Each pain point maps directly to one or more platform layers.

---

### Pain Point 1 — Terraform Creates Infrastructure, But Not a Working Data Platform

**What happens:**

Terraform apply succeeds. Engineers see green. But users cannot access catalogs, run jobs, read storage, or create schemas — because RBAC, Unity Catalog grants, service principals, storage credentials, and workspace assignments are incomplete. Unity Catalog alone requires multiple objects and role assignments spanning the Azure and Databricks control planes.

```
terraform apply  →  SUCCESS
databricks job   →  FAILED: 403 Access Denied
```

**Why it happens:**  Terraform provisions resources. It does not configure data-plane access. The gap between "resource created" and "resource usable" is manual, error-prone, and undocumented.

**DataForge solution:**  The Platform Readiness Layer closes this gap automatically.

```
Terraform (create resources)
    ↓
Ansible (configure data-plane access)
    ↓
Readiness Validation (assert everything works)
    ↓
Smoke Tests (end-to-end data write + read)
```

Validation assertions run before any environment promotion:
- `assert catalog_exists()`
- `assert schema_exists()`
- `assert storage_accessible()`
- `assert service_principal_can_read()`

**Covered by:** Layer 4 (Ansible) + Layer 5 (Readiness Validation)

---

### Pain Point 2 — Private Endpoint Hell

**What happens:**

Secure Azure environments using private endpoints break Terraform deployment order. Resources require data-plane access before private networking is fully established. Storage containers cannot be created if the storage firewall is active before the private endpoint is resolved. The failure is non-obvious and environment-specific.

```
Stage 1: Storage Account created
Stage 2: Terraform tries to create container
Stage 3: Blocked by firewall — no private endpoint yet
Stage 4: Plan fails mid-run
```

**Why it happens:**  Terraform does not natively understand Azure's private endpoint DNS propagation dependencies. Engineers must sequence resources manually — knowledge that lives in tribal memory, not code.

**DataForge solution:**  DataForge generates explicit dependency chains based on the networking configuration in the Data Product YAML. Engineers declare `private_endpoints: true`; DataForge emits the correct sequencing:

```
Stage 1: Network Foundation   (VNet, subnets, NSGs, private DNS zones)
Stage 2: Private Endpoints    (one per service — storage, databricks, keyvault)
Stage 3: Storage Containers   (only after endpoint DNS is resolvable)
Stage 4: Databricks Config    (workspace, cluster policy, mounts)
```

No engineer needs to understand Azure networking internals.

**Covered by:** Layer 3 (Terraform sequencing), Layer 2 (DataForge Engine dependency resolution)

---

### Pain Point 3 — Terraform Drift

**What happens:**

Teams deploy with Terraform, then make changes in the UI. Months later:

```
terraform plan

842 changes detected
```

Drift is the #1 CI/CD problem in shared Databricks environments. Manual UI changes to cluster policies, secrets, job configurations, and Unity Catalog grants invalidate the Terraform state silently.

**DataForge solution:**  Git is the enforced source of truth. DataForge generates:

1. **Production workspace locking** — read-only UI mode enforced via Databricks workspace policy
2. **Nightly drift detection** — scheduled `terraform plan` run; results routed to alerting channels
3. **Alert routing** — drift reports sent to Teams, Slack, or ServiceNow based on severity

```
Git  →  Source of Truth
  ↓
Nightly terraform plan (CI/CD scheduled job)
  ↓
Delta detected?  →  Alert  →  Teams / Slack / ServiceNow
No delta?        →  Platform health metric: DRIFT_FREE = true
```

**Covered by:** Layer 5 (CI/CD pipeline), Layer 8 (DriftDetectionGenerator — nightly scheduled terraform plan + alert routing)

---

### Pain Point 4 — Data Engineers Become Accidental Cloud Engineers

**What happens:**

Data engineers hired to build pipelines spend weeks on Terraform providers, RBAC, networking, private DNS zones, and role assignments. Instead of:

```python
df.write.format("delta").save(path)
```

They write:
```hcl
resource "azurerm_role_assignment" "..." { ... }
resource "azurerm_private_dns_zone_virtual_network_link" "..." { ... }
```

**Why it happens:**  There is no abstraction layer between "I need a pipeline" and "here is working infrastructure."

**DataForge solution:**  The Data Product YAML is the only interface a data engineer ever touches.

```yaml
product: customer360

source:
  type: sqlserver

target:
  type: fabric
```

DataForge generates: Terraform, Databricks, RBAC, networking, monitoring, CI/CD, governance, quality checks. The engineer never writes infrastructure code.

**Covered by:** Layer 1 (Data Product YAML intent form) + Layer 2 (DataForge Engine)

---

### Pain Point 5 — CI/CD Works in Dev But Fails in Prod

**What happens:**

Dev and prod workspaces have different catalog names, storage accounts, and endpoint configurations. Hardcoded references break promotion.

```
Dev:   catalog = sandbox,    storage = devlake
Prod:  catalog = enterprise, storage = prodlake
```

Pipeline runs successfully in dev. Fails immediately in prod with `catalog not found` or `container does not exist`.

**Why it happens:**  Developers hardcode environment-specific values. There is no abstraction layer that generates environment-correct outputs from a single source of truth.

**DataForge solution:**  The Environment Abstraction Layer generates environment-correct resources from a single YAML declaration.

```yaml
environment: prod
```

DataForge resolves this to prod catalog names, prod storage accounts, prod private endpoints, prod monitoring thresholds — with no hardcoded values in any generated file. All resource names follow `{type}-{app}-{env}` convention enforced by the DataForge Engine.

**Covered by:** Layer 2 (naming enforcement + environment mapping), `environments:` block in Data Product YAML

---

### Pain Point 6 — Production Data Quality Issues

**What happens:**

Most pipeline failures are not infrastructure failures. The pipeline deploys successfully but produces unusable data:

- Duplicate records
- Schema drift (upstream adds/removes columns silently)
- Null primary keys
- Malformed timestamps
- Late-arriving data outside the SLA window

Governance and data trust remain the biggest scaling challenges in enterprise data platforms.

**DataForge solution:**  Mandatory quality gates at each medallion layer.

```
Bronze → raw data ingested
Silver → deduplicated · validated · standardized
Gold   → business rules applied · quality score tracked
```

DataForge generates PySpark validation scripts (or Great Expectations suites) per table from the `quality.checks` section of the Data Product YAML:

```yaml
quality:
  checks:
    - layer: silver
      table: customer_events
      rules:
        - not_null: [customer_id, event_timestamp]
        - unique: [event_id]
        - accepted_values: { column: status, values: [active, churned] }
        - freshness_within: { column: event_timestamp, hours: 6 }
```

Scripts run as Databricks jobs; exit 1 blocks promotion.

**Covered by:** Layer 4 (QualityGenerator, L4 implementation) + Phase 3 roadmap

---

### Pain Point 7 — Nobody Owns Production Operations

**What happens:**

After deployment, production incidents spiral into blame triangles:

```
Platform Team:  "It's a pipeline issue."
Data Team:      "It's an infrastructure issue."
Cloud Team:     "It's a network issue."
```

No team has a complete view of pipeline health, cost, drift, or SLA compliance. There is no single owner of platform operations.

**DataForge solution:**  The Platform SRE Layer creates a defined operational ownership model with generated runbooks, dashboards, and automated alerting.

| Responsibility | Owner | Automation |
|---|---|---|
| Pipeline health | Platform SRE | Nightly health check job |
| Cost monitoring | Platform SRE | Budget alerts at 75/90/100% |
| Drift detection | Platform SRE | Scheduled terraform plan |
| Recovery automation | Platform SRE | Generated runbook per product |
| SLA tracking | Platform SRE | Freshness metric vs. SLA target |

**Key metrics tracked:**

| Metric | Target |
|---|---|
| MTTR (Mean Time to Recovery) | < 30 min |
| Pipeline Success Rate | > 99.5% |
| Data Freshness Compliance | > 99% within SLA |
| Cost per Pipeline / month | tracked, trending down |

**Covered by:** Operations Framework, Layer 6 (MonitoringGenerator), Layer 9 (SreDashboardGenerator — Azure Monitor Workbook + per-product runbook)

---

### Pain Point 8 — Cost Explosions in Production

**What happens:**

Databricks clusters are overprovisioned and left running. Retry storms multiply cluster costs 10×. Streaming state grows unbounded. Small-file compaction is forgotten. A single misconfigured autoscale bounds turns a $500/month pipeline into a $5,000/month incident.

```
Cluster running at 10% utilization
No auto-termination configured
28 days × 16 workers × $0.40/worker-hr = $4,300 wasted
```

**DataForge solution:**  Policy-based compute generated at deployment time, not discovered post-incident.

Terraform generates approved cluster policies (small / medium / large) with enforced bounds. Ansible configures:
- Auto-termination (30 min idle default)
- Runtime version pinning
- Spot instance preference in dev/test

Monitoring generates cost budget alerts:

```yaml
monitoring:
  cost:
    monthly_budget_usd: 1000
    alert_at_pct: [75, 90, 100]
```

DataForge now ships a cost optimization engine (L9):

```
Cluster running at 10% utilization for 7 days
→ Recommendation: downsize from 16 workers to 4
→ Estimated savings: $640/month
→ Action: update compute.databricks.autoscale.max_workers: 4
```

Weekly scheduled CI job queries Azure Monitor DBU metrics and Cost Management API, classifies recommendations by priority (high/medium/low), opens a GitHub issue for high-priority findings, and outputs a Rich CLI report.

**Covered by:** Layer 6 (MonitoringGenerator cost budgets), Layer 7 (Ansible cluster policy), Layer 9 (CostOptimizationGenerator — weekly rightsizing engine + scheduled CI job)

---

## The Core Shift

| | Today | North Star |
|---|---|---|
| **Input** | Natural language string | Data Product YAML |
| **Output** | Terraform + RBAC | Terraform + Ansible + CI/CD + Governance + Quality + Monitoring |
| **Who uses it** | IaC engineer | Data engineer |
| **Time to production** | 8–16 weeks | < 1 day |

---

## Data Product YAML: Two Forms

### Intent Form (zero friction — recommended entry point)

DataForge infers the full pipeline from source + target + SLA. No knowledge of nodes, edges, or Azure internals required.

```yaml
product: customer360
environment: prod

source:
  type: sqlserver

target:
  type: fabric

sla: hourly

classification:
  pii: true

retention:
  bronze: 90      # days
  silver: 365
  gold: 2555      # 7 years
```

DataForge resolves this to: `sqlserver → ADF → ADLS bronze → Databricks → ADLS silver/gold → Fabric Lakehouse` and generates everything.

### Explicit Form (full control)

For data engineers who need fine-grained pipeline control. Same generator output, explicit graph:

```yaml
apiVersion: dataforge/v1
kind: DataProduct

metadata:
  name: customer-analytics
  owner: data-engineering@company.com
  domain: marketing
  sensitivity: confidential        # public | internal | confidential | restricted
  sla:
    freshness: 4h
    availability: 99.9%

environments:
  dev:
    subscription_id: 00000000-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-customer-analytics-dev
  test:
    subscription_id: 00000000-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-customer-analytics-test
  prod:
    subscription_id: 00000000-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-customer-analytics-prod

pipeline:
  nodes:
    - id: crm_events
      type: eventhub
    - id: bronze
      type: adls
    - id: transform
      type: databricks
    - id: secrets
      type: key_vault
    - id: ingest
      type: adf
    - id: warehouse
      type: fabric_lakehouse
  edges:
    - from: crm_events
      to: ingest
      operation: stream
    - from: ingest
      to: bronze
      operation: write
    - from: ingest
      to: transform
      operation: trigger
    - from: transform
      to: bronze
      operation: read
    - from: transform
      to: secrets
      operation: secret_get
    - from: transform
      to: warehouse
      operation: write

compute:
  databricks:
    node_type: Standard_DS3_v2
    autoscale: { min_workers: 2, max_workers: 8 }
    runtime: "14.3.x-scala2.12"
    cluster_policy: job_cluster
    spot_enabled: true

storage:
  medallion:
    bronze: { retention_days: 90, immutable: true }
    silver: { retention_days: 365, schema_enforcement: true }
    gold:   { retention_days: 2555, read_only_external: true }

governance:
  unity_catalog:
    metastore: unity-catalog-prod
    catalog: customer_analytics
    schemas: [bronze, silver, gold]
    grants:
      - principal: data-engineers@company.com
        privileges: [USE_CATALOG, USE_SCHEMA, SELECT, MODIFY]
        on: catalog
      - principal: data-analysts@company.com
        privileges: [USE_CATALOG, USE_SCHEMA, SELECT]
        on: [silver, gold]
  lineage: true
  audit: true
  classification:
    pii_columns: [customer_id, email, phone]
    access_reviews: quarterly

quality:
  framework: great_expectations
  checks:
    - layer: silver
      table: customer_events
      rules:
        - not_null: [customer_id, event_timestamp]
        - unique: [event_id]
        - accepted_values: { column: event_type, values: [click, purchase, return, churn] }
        - freshness_within: { column: event_timestamp, hours: 6 }

cicd:
  provider: github_actions          # github_actions | azure_devops
  gates:
    - terraform_format
    - terraform_validate
    - checkov_scan
    - tfsec_scan
    - python_unit_tests
    - cost_estimate                 # infracost
    - policy_validation
  environments:
    - name: dev
      auto_deploy: true
    - name: test
      approval_required: true
    - name: prod
      approval_required: true
      smoke_test: true

monitoring:
  alerts:
    - { name: pipeline_failure, metric: adf_pipeline_run_failed, threshold: 1, severity: critical }
    - { name: freshness_breach, metric: data_freshness_hours, threshold: 6, severity: warning }
    - { name: quality_failure, metric: dq_rule_failed, threshold: 1, severity: critical }
  cost:
    monthly_budget_usd: 1000
    alert_at_pct: [75, 90, 100]
    chargeback_reporting: true
    monthly_optimization_report: true
  dashboards: [freshness, pipeline_health, cost_by_resource, data_quality_score]

networking:
  vnet_cidr: 10.20.0.0/16
  private_endpoints: true
  databricks_vnet_injection: true
```

---

## Platform Architecture: 5 Layers

```
Layer 1: Data Product YAML
         Intent Form or Explicit Form
                │
                ▼
Layer 2: DataForge Engine
         Validation · Naming enforcement · Security policy checks
         Environment mapping · Cost estimation
                │
         ┌──────┴──────┐──────────────┐───────────────┐
         ▼             ▼              ▼                ▼
Layer 3:          Layer 4:        Layer 5:        (repeats per env)
Terraform         Ansible         Readiness
Provisioning      Configuration   Validation
```

### Layer 2 — DataForge Engine

The engine runs before any generator. It enforces:

- **Schema validation** — required fields, enum constraints, cross-field rules
- **Naming enforcement** — all resource names conform to `{type}-{app}-{env}` convention
- **Security policy checks** — PII classification requires `sensitivity: confidential` minimum; fails fast otherwise
- **Environment mapping** — resolves intent form (source+target) to explicit FlowGraph
- **Cost estimation** — runs infracost against generated plan; blocks if over budget threshold

### Layer 3 — Terraform Provisioning

Deployment sequencing (order matters for dependency chain):

```
1. Foundation    → Resource Group, Log Analytics, Azure Monitor
2. Network       → VNet, Subnets, NSGs, Private DNS Zones, Azure Firewall
3. Security      → Key Vault, Managed Identities, RBAC assignments
4. Compute       → Databricks Workspace, AKS Cluster, ADF
5. Data Services → ADLS Gen2 filesystems, Fabric Lakehouse, EventHub
```

Resources provisioned (full list):
- Resource Groups · ADLS Gen2 (HNS) · Databricks Workspace (VNet-injected)
- Fabric Lakehouse · Key Vault · Managed Identities · Private Endpoints
- Azure Firewall · Log Analytics · Azure Monitor · EventHub · ADF · AKS

### Layer 4 — Ansible Configuration

Runs after `terraform apply`. Makes resources operational:

- Databricks cluster policies + auto-termination settings
- Unity Catalog bootstrap (metastore attach, catalog/schema creation)
- Secret scope creation in Databricks
- Monitoring agent deployment
- Linux hardening on AKS nodes
- Operational tooling (log forwarders, health check jobs)

**Principle**: Terraform creates resources. Ansible makes resources operational.

### Layer 5 — Platform Readiness Validation

Automated gate that runs after Ansible. **Deployment cannot promote to the next environment until all checks pass.**

| Check | What It Verifies |
|---|---|
| Storage access | ADLS read/write from Databricks MSI |
| Network connectivity | Private endpoint DNS resolution |
| Catalog permissions | Unity Catalog grants applied correctly |
| Secret retrieval | Key Vault secret readable by each service MSI |
| Service principal access | SP can authenticate to each downstream service |
| Data plane functionality | End-to-end: write a test row, read it back, verify |

---

## Generator Registry

The existing `Renderer` pattern extends to a full generator registry. Same `DataProduct` model drives all output.

```
DataProduct model
       │
       ├──▶ TerraformGenerator      → *.tf + rbac.tf               [L1 ✅]
       ├──▶ GovernanceGenerator     → unity_catalog.tf              [L3 ✅]
       │                              Databricks grants JSON
       ├──▶ QualityGenerator        → quality/expectations/         [L4 ✅]
       │                              databricks_jobs.tf (DQ schedulers)
       ├──▶ CiCdGenerator           → .github/workflows/            [L5 ✅]
       │                              azure-pipelines.yml
       │                              Artifacts: plan, security, cost report
       ├──▶ MonitoringGenerator     → monitoring.tf                 [L6 ✅]
       │                              cost budget + chargeback alerts
       ├──▶ AnsibleGenerator        → ansible/playbooks/            [L7 ✅]
       │                              requirements.yml + inventory.yml
       ├──▶ ReadinessGenerator      → tests/readiness/              [L8 ✅]
       │                              run_readiness.sh (blocking gate)
       ├──▶ DriftDetectionGenerator → .github/workflows/drift.yml   [L8 ✅]
       │                              scripts/drift_notify.py
       ├──▶ AdfPipelineGenerator    → adf_pipeline.tf               [ADF ✅]
       │                              linked services + datasets + triggers
       ├──▶ SreDashboardGenerator   → sre/workbook.tf               [L9 ✅]
       │                              sre/workbook.json (Azure Monitor)
       │                              sre/runbook.md (per-product)
       ├──▶ CostOptimizationGenerator → scripts/analyze_costs.py    [L9 ✅]
       │                                .github/workflows/cost.yml
       └──▶ NetworkingGenerator      → network/private_endpoints.tf [L10 ✅]
                                        network/dns.tf
                                        network/sequencing.sh  (6-stage deploy, Pain Point 2)
```

---

## CI/CD Pipeline

### Continuous Integration (every PR)

```
terraform fmt --check
terraform validate
checkov scan              ← security (IaC)
tfsec scan               ← security (additional ruleset)
python unit tests
infracost estimate        ← cost gate
policy validation         ← naming + sensitivity rules

Artifacts produced:
  terraform.plan
  security-report.json
  cost-report.json
  deployment-package.zip
```

### Continuous Delivery

```
DEV   → automatic deploy → automated tests
  ↓
TEST  → integration validation → approval gate
  ↓
PROD  → controlled release → smoke test → readiness validation
```

---

## Operations Framework

### Platform SRE Responsibilities

- **Drift detection** — scheduled `terraform plan` vs. actual state; alert on delta
- **SLA monitoring** — data freshness measured against `sla.freshness`; page on breach
- **Capacity management** — cluster autoscale bounds reviewed monthly
- **Cost optimization** — monthly report: idle resources, oversized SKUs, spot opportunity
- **Incident response** — runbooks generated per data product
- **Backup validation** — ADLS snapshot restore tested quarterly

### Key Platform Metrics

| Metric | Target |
|---|---|
| Deployment Success Rate | > 98% |
| Pipeline Reliability | > 99.5% |
| Mean Time To Recovery | < 30 min |
| Cost Per Data Product / month | tracked, trending down |
| Data Freshness Compliance | > 99% within SLA |
| Data Quality Score | > 95% rules passing |

### Cost Management Controls

- Cluster auto-termination enforced via Databricks cluster policy (generated)
- Spot instances enabled by default in dev/test
- Per-product budget alerts at 75% / 90% / 100%
- Chargeback tags on all resources (`cost-center`, `data-product`, `environment`)
- Monthly optimization report: rightsizing + idle resource recommendations

---

## Roadmap

| Phase | Timeline | Scope |
|---|---|---|
| **Phase 1** ✅ | 0–3 months | Data product schema · Terraform generation · Azure deployment · CI/CD integration (L1–L3) |
| **Phase 2** ✅ | 3–6 months | Ansible automation · Readiness validation · Drift detection · ADF pipelines (L4–L8 + ADF) |
| **Phase 3** ✅ | 6–12 months | Cost optimization engine · Platform SRE workbook · Per-product runbooks (L9) |
| **Phase 4** ✅ | 12+ months | Networking generator · Self-service portal · Multi-LLM support (L10) |
| **Phase 5** | Future | AI-assisted deployment recommendations · Automated remediation · Multi-cloud |

---

## Loop Evolution (Implementation Iterations)

Each loop = one generator layer + tests + end-to-end verification. Schema locked from L1.

| Loop | Phase | Delivers | What You Learn |
|---|---|---|---|
| **L1** ✅ | 1 | NL → Terraform + RBAC | RBAC is deterministic, not LLM work |
| **L2** ✅ | 1 | YAML input path + deterministic intent resolver | YAML → FlowGraph bridge; schema validation |
| **L3** ✅ | 1 | Unity Catalog + governance generator | Governance as code: metastore → catalog → grants |
| **L4** ✅ | 2 | PySpark data quality scripts + Databricks job TF | Production-readiness for data, not just infra |
| **L5** ✅ | 2 | CI/CD generator (GHA + ADO) with security gates | Multi-env promotion with checkov, tfsec, infracost |
| **L6** ✅ | 2 | Monitoring + cost budget generator | Azure Monitor alerts wired at deploy time, not post-incident |
| **L7** ✅ | 2 | Ansible playbooks (REST API, Unity Catalog bootstrap) | Gap between `terraform apply` and a running platform |
| **L8** ✅ | 2 | Readiness gate suite + drift detection CI/CD | What "deployment complete" actually means; Pain Points 1 & 3 |
| **ADF** ✅ | 2 | ADF pipeline: linked services + datasets + triggers | Data-plane: infrastructure exists but nothing moves data |
| **L9** ✅ | 3 | Cost optimization engine + SRE workbook + runbook | Platform operations as a product; Pain Points 7 & 8 |
| **L10** ✅ | 4 | Networking generator + self-service portal + multi-LLM support | End-to-end: non-engineer deploys a data product in < 1 day |

---

## Expected Outcomes

- **70–90% reduction** in platform provisioning effort
- New data products onboarded in **< 1 day** vs. 8–16 weeks
- **Reduced production incidents** through consistent governance enforcement from day 1
- **Lower cloud costs** through generated cluster policies, auto-termination, and budget guardrails
- **Self-service** — data engineers own their platform without infrastructure expertise

---

## Current State vs. North Star

```
Infrastructure + RBAC:      ████████████████████  100% ✅ L1
YAML input + intent form:   ████████████████████  100% ✅ L2
Governance (Unity Catalog): ████████████████████  100% ✅ L3
Data quality scripts:       ████████████████████  100% ✅ L4
CI/CD generation:           ████████████████████  100% ✅ L5
Monitoring + cost budgets:  ████████████████████  100% ✅ L6
Ansible configuration:      ████████████████████  100% ✅ L7
Readiness validation:       ████████████████████  100% ✅ L8
Drift detection:            ████████████████████  100% ✅ L8
ADF pipeline generation:    ████████████████████  100% ✅ ADF
Cost optimization engine:   ████████████████████  100% ✅ L9
SRE dashboard + runbooks:   ████████████████████  100% ✅ L9
Networking generator:       ████████████████████  100% ✅ L10  (Pain Point 2)
Self-service portal:        ████████████████████  100% ✅ L10  (North Star end state)
Multi-LLM support:          ████████████████████  100% ✅ L10  (Anthropic · OpenAI · Groq · Ollama · Mistral)
```

## Principles

1. **YAML is the contract** — engineers describe intent, DataForge owns implementation
2. **Deterministic > AI** — RBAC, Unity grants, DQ rules, naming: never LLM-generated
3. **AI for ambiguity only** — NL parsing and intent-form resolution are the only AI-touched layers
4. **Generators are independent** — each produces its own output, no coupling between them
5. **Secure defaults always** — omitting a section never produces an insecure output
6. **Readiness is a gate** — environments cannot promote until all validation checks pass
7. **Each loop ships something real** — no speculative architecture; every iteration is verified end-to-end
