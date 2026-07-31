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

## Local fixture database (development only)

Without EnzymeX access there is no copy to point at, so `scripts/dev_seed_fixture.py`
builds a stand-in with the same schema. It is not part of the deployment path.
Any MySQL 8+ works; the steps below use a throwaway conda instance so nothing is
installed system-wide.

```bash
micromamba create -y -n mysql-fixture -c conda-forge mysql-server
DD=$HOME/mysql-fixture-data
micromamba run -n mysql-fixture mysqld --initialize-insecure --datadir=$DD --user=$(whoami)
setsid nohup micromamba run -n mysql-fixture mysqld \
  --datadir=$DD --port=3307 --socket=$DD/mysql.sock --bind-address=127.0.0.1 --mysqlx=0 \
  > $DD/server.log 2>&1 < /dev/null &
```

The conda `mysql-server` package ships no `mysql` client, so create the database
and the read-only account with pymysql from the project environment:

```bash
micromamba run -n blast-hmmer-datax python - <<'PY'
import pymysql
c = pymysql.connect(host="127.0.0.1", port=3307, user="root", password="")
with c.cursor() as cur:
    cur.execute("CREATE DATABASE IF NOT EXISTS enzymex_copy CHARACTER SET utf8mb4")
    cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", ("enzymex_ro", "%", "fixture_ro_pw"))
    cur.execute("GRANT SELECT ON enzymex_copy.* TO %s@%s", ("enzymex_ro", "%"))
    cur.execute("FLUSH PRIVILEGES")
PY

python scripts/dev_seed_fixture.py --port 3307 --user root --password ""
```

The seed downloads reviewed UniProtKB entries for twelve EC numbers (~15 s) and
caches them in `var/fixture/uniprot.json`, so a re-seed needs no network. It
loads 2,677 rows: 2,667 clean, 10 deliberately damaged, plus a 111-sequence
holdout written to `var/fixture/holdout_queries.fasta` that is excluded from the
table and therefore usable as positive queries.

Point `.env` at it with `ENZYMEX_DB_PORT=3307`, `ENZYMEX_DB_USER=enzymex_ro`,
`ENZYMEX_DB_PASSWORD=fixture_ro_pw` and `ENZYMEX_DB_CONFIRM_COPY=true`.

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
  "engine": "InnoDB",
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
| accession | `accession`, `acc`, `uniprot`, `uniprot_id`, `uniprotid`, `entry`, `identifier` |
| motif / active / binding / interpretation | the obvious variants |

`sequence` and `source` are required. The source column is needed to enforce
the Swiss-Prot + PDB reference policy; a missing source fails closed instead
of silently admitting unrelated records. Other absent fields are reported by
`inspect` and exported as null.

**A stable export key is required.** For a table, use its primary key. If the
copy has no key, export through a view with a deterministic unique column
named `id`, which the schema detector accepts as the export key. Internal
identifiers are `EXR<key>` and rows are ordered by that value. Without it,
`iter_rows` refuses rather than producing a build that changes between runs.

Table and column names are validated against `[A-Za-z0-9_$]` before being
interpolated into any statement. Identifiers cannot be passed as bound
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
| `source_not_selected` | valid sequence, but its normalized source is outside `ENZYMEX_REFERENCE_SOURCES` |
| `duplicate_sequence` | identical to a selected sequence already exported |

Sequence validation runs before source selection, and deduplication runs after
it. This preserves useful QC reasons for malformed rows while preventing an
earlier TrEMBL or KEGG copy from hiding a valid Swiss-Prot or PDB reference.

Duplicates are merged rather than dropped: the extra rows go into
`reference_duplicate` in the metadata database, so a hit can still be traced
back to every selected `enzymesdata` row it represents. When an exact sequence
occurs in both selected sources, the Swiss-Prot row is preferred as canonical
metadata and the PDB row remains duplicate provenance; fields are not merged.
The exporter makes two passes over one repeatable-read, read-only snapshot so
priority is independent of export-key order. A physical table must use InnoDB;
for a view, the operator must confirm that its source tables are transactional.

Sequences are normalised before validation (case folded, whitespace and
residue numbering removed, gap characters stripped), so a row stored as
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
