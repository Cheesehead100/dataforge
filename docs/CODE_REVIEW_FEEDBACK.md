# Code Review Feedback

This review focuses on issues that can break the advertised `dataforge generate`
workflow or produce unsafe generated infrastructure. Line references are based on
the current `main` branch at the time this branch was created.

## 1. CLI import fails because `OutputWriter` is missing

**Severity:** P1

`src/dataforge/cli.py` imports `dataforge.output.writer.OutputWriter`, but the
repository does not contain `src/dataforge/output`.

```python
from dataforge.output.writer import OutputWriter
```

Because this import happens at module load time, every CLI command fails before
Click can dispatch to a subcommand.

**Suggested change**

Add a small output writer module or use the existing `TerraformFile.write_to`
method consistently. The writer should:

- create the output directory when it does not exist;
- refuse to overwrite existing files unless `--overwrite` is set;
- create nested directories for files such as `quality/...` and
  `.github/workflows/...`;
- return the written `Path` objects so the CLI can print them.

Example shape:

```python
class OutputWriter:
    def write(self, files: list[TerraformFile], directory: Path, *, overwrite: bool) -> list[Path]:
        if directory.exists() and any(directory.iterdir()) and not overwrite:
            raise FileExistsError(f"{directory} already exists and is not empty; use --overwrite")

        directory.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for file in files:
            path = directory / file.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file.content, encoding="utf-8")
            paths.append(path)
        return paths
```

## 2. Explicit YAML edge direction conflicts with RBAC direction

**Severity:** P1

`FlowEdge.source` is documented as the principal/caller and
`FlowEdge.target` as the scope/callee. `RbacResolver` relies on that contract and
skips edges whose source cannot hold a managed identity.

```python
principal_node = graph.node(edge.source)
scope_node = graph.node(edge.target)

if principal_node.type not in PRINCIPAL_NODE_TYPES:
    continue
```

However, explicit-form YAML tests use natural data-flow direction. For example,
`adls -> databricks` with operation `read` means "Databricks reads ADLS", but the
resolver sees ADLS as the principal and skips the edge.

```python
edges=[("src", "dbx", "read")]
```

This can silently omit required role assignments such as Databricks' Storage Blob
Data Reader role on ADLS.

**Suggested change**

Pick one edge contract and enforce it everywhere. The least surprising YAML
contract is probably data-flow direction, but RBAC generation then needs to
derive the principal from the operation:

- `read`: principal is the consumer, scope is the producer;
- `write`: principal is the producer, scope is the target;
- `trigger` and `secret_get`: principal remains the caller/source.

Alternatively, keep the current principal/scope contract and rename the YAML
fields away from `from`/`to`, because those strongly imply data flow.

Add tests that resolve explicit YAML and assert the actual RBAC assignment, not
only the graph shape.

## 3. Checkov validation can report success when Checkov is unavailable

**Severity:** P1

`CheckovRunner` invokes `python -m checkov`, but missing modules do not raise
`FileNotFoundError`. Python exits non-zero and writes the error to stderr. The
runner only parses stdout, so empty stdout becomes a zero-failure report.

```python
result = subprocess.run(...)
raw = result.stdout
return self._parse(raw)
```

Since `checkov` is not listed in project dependencies, this can make the default
`generate` validation path look successful even though no validation ran.

**Suggested change**

Treat non-zero exit codes with empty or invalid JSON as a failed or skipped
validation state that is clearly surfaced to the caller. Either add `checkov` to
a dedicated optional dependency, or make `--no-validate` the default unless the
dependency is installed.

Example direction:

```python
if result.returncode != 0 and not result.stdout.strip():
    return CheckovReport(
        failed=1,
        raw_output=result.stderr or "checkov failed without JSON output",
    )
```

Also add a regression test that mocks `subprocess.run` returning
`returncode=1`, empty stdout, and `No module named checkov` on stderr.

## 4. `generate --from ... --json-output` references an undefined variable

**Severity:** P2

In the YAML path, the CLI builds `tf_result`, `platform_result`, `all_files`, and
`all_warnings`, but the JSON output block references `result`.

```python
out = {"files": [f.filename for f in result.files], "warnings": result.warnings}
```

That branch will raise `NameError` after writing files.

**Suggested change**

Use the already merged values:

```python
out = {"files": [f.filename for f in all_files], "warnings": all_warnings}
```

## 5. SQL MI template emits an unresolved Terraform placeholder

**Severity:** P2

`sql_mi.tf.j2` emits an unquoted placeholder for `subnet_id`:

```hcl
subnet_id = TODO_REPLACE_WITH_SQL_MI_SUBNET_ID
```

The README's first example uses `source.type: sqlserver`, which renders SQL MI.
The YAML path disables LLM polishing, so the zero-AI path can produce Terraform
that cannot validate or apply.

**Suggested change**

Make SQL MI subnet configuration explicit in `data-product.yaml` or generate the
required subnet as part of networking. If the value must remain user supplied,
emit a Terraform variable instead of an invalid placeholder:

```hcl
variable "sql_mi_subnet_id" {
  description = "Dedicated subnet ID for SQL Managed Instance"
  type        = string
}

subnet_id = var.sql_mi_subnet_id
```

Then add an integration assertion that generated Terraform contains no
`TODO_REPLACE` or bare placeholder tokens.

