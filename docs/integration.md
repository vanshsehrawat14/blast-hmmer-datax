# Integrating into EnzymeX later

> This repository is an independent test environment. It is not the official
> EnzymeX production codebase and does not modify the live EnzymeX service.
> Nothing here has been deployed to, or pushed at, the EnzymeX repository.

## Portable core and required adapters

The search logic under `app/search/`, plus `app/fasta.py` and `app/schemas.py`,
is framework-independent. The reference builder is also reusable against a
compatible copied schema. EnzymeX still needs adapters for its settings,
scheduler, job/result persistence, artifact directories and result page. The
dependency edge runs one way:

```
app/web/  ──►  app/search/  ──►  app/references/  ──►  app/config.py
 (drop)          (keep)             (keep)               (merge)
```

The call a host application makes is:

```python
from app.fasta import parse_submission
from app.search.service import run_search

records = parse_submission(fasta_text, max_sequences=10, max_length=5000)
result  = run_search(settings, records, ["blastp", "phmmer", "hmmscan"])
# result is a JobResult; result.flat_rows() is the tabular form
```

`run_search` is synchronous, manages standalone job directories and returns a
Pydantic model. This call is the integration boundary, but the Pyramid
application must adapt it to EnzymeX's existing scheduled job, `job`,
`job_result`, `ref_data`, temporary-directory and result-page behavior.

## Confirmed from the EnzymeX repository, 2026-08-05

Access to `datax-lab/enzymex` was granted on 2026-08-05. The following is read
from that source, not inferred. Everything else in this document that is not
listed here remains as written, including the parts still marked inferred.

**The real `enzymesdata` DDL** — `pull_data/pull_ecpick_data.py:83-97`:

```sql
CREATE TABLE temp_enzymesdata(
	Description text Not NULL,
    Sequence Text NOT NULL,
    EC varchar(15) NOT NULL,
    Motif text,
    Active text,
    Binding text,
    Interpretation text,
    Source text NOT NULL,
    Modified date NOT NULL,
    Created date NOT NULL,
    UniprotID text NOT NULL
    );
```

No primary key, no `id`, no index, no `ENGINE` clause. The risk pre-flagged in
item 1 below is therefore confirmed, not hypothetical: `iter_rows` in
`app/references/db.py` raises rather than exporting from an unkeyed table, and
the copy-side view is the required path, not a contingency.

**`UniprotID` is a real `NOT NULL` column**, not an identifier buried in the
description. It is the basis of the content-derived export key and it corrects
item 7, which was written before the column was confirmed to exist. It is
**not unique on its own** — see the measured counts below.

**The refresh is destructive** — same file, lines 76-101: `DROP TABLE
enzymesdata`, then `RENAME TABLE temp_enzymesdata TO enzymesdata`, then
recreate `temp_enzymesdata`. Three consequences the builder has to survive:
there is a window in which `enzymesdata` does not exist, so a scheduled build
can land in it; any copy-side view over the table is invalidated by every
refresh and has to be recreated; and row order is not stable across refreshes,
so a positional key would silently re-point `EXR…` identifiers at different
proteins. This is the argument for keying on `UniprotID` rather than row
position.

**The real source vocabulary is `Swiss-Prot`, `PDB`, `KEGG`, `Test`** —
`views/base.py:90, 203, 219-366` and confirmed against the data below. Casing
is inconsistent both in the upstream queries (`'Swiss-Prot'` at line 229,
`'swiss-prot'` at line 306) and between the queries and the stored values
(`'TEST'` is queried, `Test` is stored), which only works because the table's
collation is `utf8mb4_0900_ai_ci`. `normalize_source` in
`app/references/export.py` lowercases before matching and was re-run against
these exact values: `Swiss-Prot` → `swissprot` and `PDB` → `pdb` are exported,
`KEGG` and `Test` are excluded. No change needed.

TrEMBL is not a `Source` value. It lives in a **separate `trembldata` table**
(170,502 rows, a different and smaller column set: no `UniprotID`, everything
nullable). Handoff documents that describe `enzymesdata` as including TrEMBL
are describing the database, not the table. Nothing in this project reads it.

**The repository is a partial subtree of the deployed application**, not a
buildable project. It contains `templates/`, `views/`, `schedule/`,
`pull_data/` and `README.md`. Absent, and never present in history
(`git log --all --diff-filter=D` returns nothing for any of them): `setup.py`,
any `.ini`, `requirements.txt`, `ecpick/__init__.py`, `ecpick/models/`,
`ecpick/csv_to_fasta.py`, `static/`.

Those files are not lost — they are in the local-server archive
(`ECPICK-Python.zip`), which carries the full package root: `setup.py`,
`production.ini`, `pytest.ini`, `ecpick/models/`, `dependent.txt` and
`ecpick_ddl.sql`. So the answer to "does the build make sense" is that the
GitHub repository is a subset of a working Pyramid application, not a broken
project: `setup.py` declares Pyramid, SQLAlchemy, alembic, apscheduler,
biopython and mysql-connector-python, exposes `main = ecpick:main` as the
`paste.app_factory`, and `production.ini` serves it under waitress on 6543 in
development and uwsgi from `/var/www/ecpick-python/ECPICK-Python-master` in
production. The archive is dated 2024 and predates the `schedule/` code in the
GitHub repository, so it is a reference for structure, not the current tree.

## Confirmed from the production database dump

Read from `enzymex.sql` (mysqldump 10.13, MySQL 8.0.43, database
`ecpick_python`), which supersedes the inferred figures wherever the two
disagree. The dump is not in this repository and must not be committed.

The deployed `enzymesdata` DDL matches `pull_ecpick_data.py` exactly and adds
what the script omits: `ENGINE=InnoDB`, `CHARSET=utf8mb4`,
`COLLATE=utf8mb4_0900_ai_ci`. **InnoDB satisfies the two-pass export's
transactional requirement**, and the absence of a primary key is confirmed at
the deployed level, not just in the statement that creates the table.

| | rows |
|---|---:|
| `enzymesdata` total | 561,510 |
| — `Swiss-Prot` | 302,500 |
| — `PDB` | 152,480 |
| — `KEGG` (excluded) | 87,441 |
| — `Test` (excluded) | 19,089 |
| **exported under the current source policy** | **454,980** |
| distinct sequences within that | 280,309 |
| `trembldata` | 170,502 |
| `ref_data` | 1,145 |
| `job` / `job_result` | 449 / 372 |
| `user` | 1 |

Exact-sequence deduplication removes 174,671 rows, so a full build produces
roughly **280,309 references** — about 100× the 1,574-reference fixture every
runtime figure in these docs was measured on. Re-measure before quoting any of
them. 5,643 sequences are shorter than `ENZYMEX_MIN_SEQUENCE_LENGTH` (30) and
13 exceed `ENZYMEX_MAX_REFERENCE_LENGTH` (10,000); the longest is 35,213
residues. No row has a blank `EC`.

**The table is one row per (protein, EC), not one row per protein.** This is
the finding that decides the export design. Over the 454,980 exportable rows
there are 404,340 distinct `UniprotID` values, and **no `UniprotID` ever
carries two different sequences** — zero, measured. A `UniprotID` repeats
because the protein has more than one EC number, each on its own row:

```
1A49_1  2.7.10.2  PDB  PYRUVATE KINASE  530 aa
1A49_1  2.7.1.40  PDB  PYRUVATE KINASE  530 aa
1A49_1  2.7.11.1  PDB  PYRUVATE KINASE  530 aa
```

Adding `Sequence` to the key therefore buys nothing, and `(UniprotID, EC)` is
not unique either — 757 groups are exact duplicate rows, 1,001 rows in total.
`Source` is constant per protein (zero `UniprotID`s span more than one).

So the copy-side view must **group by `UniprotID`** and fold the EC values into
the `;`-separated form `normalize_ec` already parses, exposing `UniprotID` as
`id`. That yields 404,340 keyed references, 37,545 of which carry more than one
EC. Grouping is not cosmetic: without it, sequence deduplication would collapse
a multi-EC protein's rows into a single reference and keep only one of its EC
numbers, which for a page that prints EC next to every hit is silent data loss.
The longest folded EC string is 107 characters, comfortably inside MySQL's
default `group_concat_max_len` of 1024, but a view that relies on
`GROUP_CONCAT` should assert that rather than assume it.

The `user` table holds a single row, so the "secured user accounts data"
mentioned in the handover is one administrative account rather than a user
base. It still never enters this project: the export reads `enzymesdata` only,
and the dump stays out of git and out of any fixture.

**Item 6 stays blocked.** `execute_diamond` is imported from
`ecpick.csv_to_fasta` by `schedule/hitec_predictor.py:26`,
`schedule/exception_predictor.py:28` and
`schedule/exception_hitec_predictor.py:26`, and that module is one of the
absent ones. The DIAMOND parameters cannot be reconciled until it is available.

## What must change once EnzymeX repository access is granted

1. **Confirm the real `enzymesdata` schema.** *Done — see above; the table has
   no primary key, so the copy-side view is required.* The alias table in
   `app/references/db.py` was written against the documented column list and
   verified against a synthetic fixture. Still run `enzymex-refbuild inspect`
   against the actual copy: the DDL gives the columns, but not the storage
   engine, the row count, or whether the deployed table has drifted from the
   statement that creates it.

   The exporter fails closed without a source column and selects only
   normalized `swissprot` and `pdb` rows. The handoff documents say
   `enzymesdata` contains only subsets of the upstream databases, so a
   successful build must not be described as the complete Swiss-Prot or PDB
   corpus.

   The two-pass export requires InnoDB or a view whose source tables are
   transactional. The builder rejects a physical table with another reported
   engine; inspect the underlying tables separately when exporting through a
   view.

2. **Re-measure the build.** Every runtime figure in these docs comes from a
   1,574-reference synthetic fixture build; the real table yields roughly
   280,309 references after deduplication, about 100× that. The clustering
   thresholds and QC gates are defaults chosen on the small scale; on the real
   table, check
   `skipped_clusters.tsv` and
   `profile_member_coverage` in the manifest before trusting the profile layer,
   and expect `ENZYMEX_PROFILE_MIN_MEMBERS` to need raising if the number of
   accepted clusters makes the MAFFT loop too slow (see
   `docs/reference-build.md`).

3. **Decide where the reference build runs.** It needs read access to the
   copied database and write access to an artifact directory. It must not run
   inside a request. A cron job or a manual step after each database refresh
   is enough; the build id makes it obvious when the artifacts are stale.
   The standalone `all` command replaces export, BLAST and HMMER artifacts in
   stages, so keep its web process stopped during rebuilds. For EnzymeX, build
   a complete versioned generation and switch an active pointer only after all
   artifacts pass validation.

4. **Replace `app/web/` with EnzymeX views and templates.** The results page
   here is a reference implementation of *what to show*, not markup to lift.
   The three tables are deliberately separate (see the E-value comparability
   note in `docs/science.md`) and that separation should survive the port.

5. **Decide on execution mode.** Synchronous is fine at the scale measured
   here. EnzymeX already runs ECPICK, HIT-EC and CLEAN through a scheduler; if
   BLAST/HMMER results are to appear on the same result page, the natural
   thing is to run them as additional steps in that existing job rather than
   in the request. `run_search` is a plain function and will work either way.

6. **Reconcile with DIAMOND.** EnzymeX already uses DIAMOND internally to
   retrieve up to 20 similar proteins for interpretation, with a documented
   30% similarity cutoff and a same-predicted-EC random fallback when no hit is
   found. DIAMOND and blastp answer the same question at
   different sensitivity, so on the real page they should either be presented
   together with the difference labelled, or one of them should be dropped.
   Showing both without explanation invites the reader to treat two E-values
   from different tools as comparable. The proof-of-concept comparison in
   `results/comparison/comparison_report.md` quantifies the difference on a
   small set: DIAMOND's default mode missed the most distant positive query
   that `--very-sensitive`, BLAST and HMMER all found.

7. **Settle identifier policy.** *Corrected by the confirmed DDL.* References
   here are `EXR<stable copy/view key>`. The real table has no primary key, so
   the copy-side view must expose a deterministic unique key as `id`, which the
   schema detector accepts as the export key. `source_pk` stores that key.

   `UniprotID` is a real `NOT NULL` column, so it — not row position — is what
   that key should be derived from; a positional key would re-point identifiers
   at different proteins on every destructive refresh. It repeats across raw
   rows only because a protein's EC numbers are stored one per row, so once the
   view groups by `UniprotID` it is a genuine key over 404,340 references.
   Preserve it in a dedicated metadata field rather than leaving it embedded in
   `description`. Keep the safe internal ID for BLAST/HMMER deflines.

## Suggested order of work

1. Point `enzymex-refbuild inspect` at the real copy; verify its primary key,
   storage engine, source vocabulary, `UniprotID` mapping and representative
   PDB identifiers.
2. Run a full build with `ENZYMEX_EXPORT_LIMIT` set, then unset. Compare the
   manifests.
3. Wire `run_search` into a scratch EnzymeX view and check hits resolve to the
   right EC and source.
4. Port the results markup into the EnzymeX result page alongside the model
   predictions.
5. Decide DIAMOND's fate.
6. Move the build to whatever schedule the database refresh follows.

## Known limitations to carry across

* Profile HMMs cover only QC-passing clusters, never the whole reference set.
  The page must keep saying so.
* `phmmer` is the slowest method and scales with reference count. It is the
  first thing to hit a timeout on a large copy.
* There is no authentication, rate limiting or CSRF protection here; EnzymeX
  presumably has its own, and this code assumes it.
* Job results are files on disk with a 24-hour retention, which is a test
  server's answer, not a production one.
