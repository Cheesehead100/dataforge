# DataForge North Star: Enterprise Data Product Platform

## Success Metric

> A data engineer requests a new production-ready data platform using a single declarative configuration — without understanding Terraform, networking, or cloud infrastructure internals.

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
       ├──▶ TerraformGenerator     → *.tf + rbac.tf           [L1 ✅]
       ├──▶ GovernanceGenerator    → unity_catalog.tf          [L3]
       │                             Databricks grants JSON
       ├──▶ QualityGenerator       → quality/expectations/     [L4]
       ├──▶ CiCdGenerator          → .github/workflows/        [L5]
       │                             azdo/pipelines/
       │                             Artifacts: plan, security report, cost report
       ├──▶ MonitoringGenerator    → monitoring.tf             [L6]
       │                             cost budget + chargeback alerts
       ├──▶ AnsibleGenerator       → ansible/playbooks/        [L7]
       └──▶ ReadinessGenerator     → tests/readiness/          [L8]
                                     per-env blocking gate suite
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
| **Phase 1** ✅ | 0–3 months | Data product schema · Terraform generation · Azure deployment · CI/CD integration |
| **Phase 2** | 3–6 months | Ansible automation · Readiness validation · Security automation · Drift detection |
| **Phase 3** | 6–12 months | Data quality framework · Governance automation · Cost optimization engine · Platform SRE dashboard |
| **Phase 4** | 12+ months | Self-service portal · AI-assisted deployment recommendations · Automated remediation · Multi-cloud |

---

## Loop Evolution (Implementation Iterations)

Each loop = one generator layer + tests + end-to-end verification. Schema locked from L1.

| Loop | Phase | Delivers | What You Learn |
|---|---|---|---|
| **L1** ✅ | 1 | NL → Terraform + RBAC | RBAC is deterministic, not LLM work |
| **L2** | 1 | YAML input path + intent resolver | YAML → FlowGraph bridge; schema validation |
| **L3** | 2 | CI/CD generator (GHA + ADO) | Multi-env promotion with all 7 gates |
| **L4** | 2 | Ansible generator | Gap between `terraform apply` and a running platform |
| **L5** | 2 | Readiness validation suite | What "deployment complete" actually means |
| **L6** | 2 | Drift detection | State divergence patterns in real deployments |
| **L7** | 3 | Unity Catalog + governance | Governance as code: metastore → catalog → grants |
| **L8** | 3 | Data quality framework | Production-readiness for data, not just infra |
| **L9** | 3 | Cost optimization engine + SRE dashboard | Platform operations as a product |
| **L10** | 4 | Self-service portal | End-to-end: non-engineer can deploy a data product |

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
Infrastructure + RBAC:      ████████████████████  100% (L1 ✅)
YAML input + intent form:   ░░░░░░░░░░░░░░░░░░░░    0% (L2 next)
CI/CD generation:           ████░░░░░░░░░░░░░░░░   20% (ADO template exists)
Ansible:                    ░░░░░░░░░░░░░░░░░░░░    0% (L4)
Readiness validation:       ░░░░░░░░░░░░░░░░░░░░    0% (L5)
Drift detection:            ░░░░░░░░░░░░░░░░░░░░    0% (L6)
Governance (Unity):         ░░░░░░░░░░░░░░░░░░░░    0% (L7)
Data quality:               ░░░░░░░░░░░░░░░░░░░░    0% (L8)
Cost optimization engine:   ░░░░░░░░░░░░░░░░░░░░    0% (L9)
Self-service portal:        ░░░░░░░░░░░░░░░░░░░░    0% (L10)
```

## Principles

1. **YAML is the contract** — engineers describe intent, DataForge owns implementation
2. **Deterministic > AI** — RBAC, Unity grants, DQ rules, naming: never LLM-generated
3. **AI for ambiguity only** — NL parsing and intent-form resolution are the only AI-touched layers
4. **Generators are independent** — each produces its own output, no coupling between them
5. **Secure defaults always** — omitting a section never produces an insecure output
6. **Readiness is a gate** — environments cannot promote until all validation checks pass
7. **Each loop ships something real** — no speculative architecture; every iteration is verified end-to-end
