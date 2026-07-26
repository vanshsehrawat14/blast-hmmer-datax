# Copied database

This server reads a **copy** of the EnzymeX MySQL database. Never production.

## Grant a read-only account

```sql
CREATE USER 'enzymex_ro'@'%' IDENTIFIED BY '<strong-password>';
GRANT SELECT ON enzymex_copy.* TO 'enzymex_ro'@'%';
FLUSH PRIVILEGES;
```

`SELECT` is the only privilege the reference build needs. On top of that
grant, every session it opens issues `SET SESSION TRANSACTION READ ONLY`, so
even an over-privileged account cannot write from this code.

## Configure

```bash
cp .env.example .env
```

```bash
ENZYMEX_DB_HOST=10.0.0.5
ENZYMEX_DB_PORT=3306
ENZYMEX_DB_NAME=enzymex_copy
ENZYMEX_DB_USER=enzymex_ro
ENZYMEX_DB_PASSWORD=...
ENZYMEX_DB_TABLE=enzymesdata

# Required. The build refuses to connect without it.
ENZYMEX_DB_CONFIRM_COPY=true
```

`.env` is gitignored. The password is held as a `SecretStr`: it is not in
`repr(settings)`, not in any log line, and not in `/health`, which reports
only `user@host:port/database`.

`ENZYMEX_DB_CONFIRM_COPY` exists because the difference between the copy and
production is one hostname. Requiring a second, explicit variable means that
pointing the tool at production takes two deliberate edits, not one careless
one.

## Inspect before building

```bash
make inspect          # or: enzymex-refbuild inspect --out var/table_report.json
```

Read-only. It reports row count, primary key, which documented columns are
actually present, null and empty sequence counts, sequence length
distribution, duplicate sequence count, missing and multi-valued EC counts,
the commonest EC values, and the source breakdown.

Sample output from the development fixture:

```json
{
  "table": "enzymesdata",
  "rows": 2677,
  "primary_key": "id",
  "columns_present": {
    "sequence": "sequence", "description": "description", "ec": "ec",
    "source": "source", "motif": "motif", "active": "active",
    "binding": "binding", "interpretation": "interpretation",
    "created": "created", "modified": "modified", "accession": "accession"
  },
  "columns_missing": [],
  "null_sequences": 1,
  "empty_sequences": 1,
  "sequence_length": {"min": 3, "max": 1861, "mean": 355.3},
  "duplicate_sequences": 247,
  "missing_ec": 1,
  "multi_ec_rows": 217
}
```

## Schema expectations

The handover notes document `enzymesdata` as carrying description, sequence,
EC, motif, active, binding, interpretation, source, modified and created. The
code does not assume those exact names. `app/references/db.py` resolves each
logical field case-insensitively against a list of plausible aliases:

| logical field | accepted column names |
|---|---|
| sequence | `sequence`, `seq`, `protein_sequence`, `aa_sequence` |
| description | `description`, `desc`, `name`, `protein_name`, `title` |
| ec | `ec`, `ec_number`, `ecnumber`, `ec_num` |
| source | `source`, `db_source`, `database`, `origin` |
| accession | `accession`, `acc`, `uniprot`, `uniprot_id`, `entry`, `identifier` |
| motif / active / binding / interpretation | the obvious variants |

Only `sequence` is required. Anything absent is reported by `inspect` and
exported as null; the build does not fail over a missing optional column.

**A primary key is required.** Internal reference identifiers are `EXR<pk>`,
and the export is ordered by that key. Without one there is no stable order
and no stable identifier, so `iter_rows` refuses rather than producing a
build that silently changes between runs. If the copy has no key, export from
a view that supplies one.

Table and column names are validated against `[A-Za-z0-9_$]` before being
interpolated into any statement — identifiers cannot be passed as bound
parameters, so they are checked instead of quoted-and-hoped. All values are
bound.

## Data quality the export handles

Every row that does not become a reference is counted by reason in
`var/reference/export_stats.json` and listed in `var/reference/skipped.tsv`:

| reason | meaning |
|---|---|
| `null_sequence` | the sequence column is NULL |
| `empty_sequence` | whitespace only |
| `too_short` / `too_long` | outside `ENZYMEX_MIN_SEQUENCE_LENGTH` / `ENZYMEX_MAX_REFERENCE_LENGTH` |
| `invalid_characters` | letters outside the IUPAC protein alphabet |
| `internal_stop` | `*` inside the sequence (a trailing `*` is stripped, not rejected) |
| `excessive_ambiguity` | more than `ENZYMEX_MAX_AMBIGUOUS_FRACTION` of X/B/Z/J/U/O |
| `looks_like_nucleotide` | ≥90% A/C/G/T/U/N over ≥50 residues |
| `duplicate_sequence` | identical to a sequence already exported |

Duplicates are merged rather than dropped: the extra rows go into
`reference_duplicate` in the metadata database, so a hit can still be traced
back to every `enzymesdata` row it represents.

Sequences are normalised before validation — case folded, whitespace and
residue numbering removed, gap characters stripped — so a row stored as
wrapped FASTA with column numbers still exports correctly.

EC values are normalised to a sorted, deduplicated, semicolon-joined string.
Partial (`1.1.-.-`) and preliminary (`1.1.1.n5`) EC numbers are valid IUBMB
forms and are kept; unparseable text is treated as unannotated. Multi-EC rows
keep every EC and are counted in the manifest.

## What is never done

* No `INSERT`, `UPDATE`, `DELETE`, `ALTER` or `DROP` anywhere in the codebase.
* No schema change to the copy.
* No packaging or redistribution of the copied data. `var/` is gitignored in
  full and the Docker image mounts artifacts from a volume rather than baking
  them in.
* No database access from a user request. See `docs/architecture.md`.
