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
with a different and smaller column set: no `UniprotID`, everything nullable.
Handoff documents that describe `enzymesdata` as including TrEMBL are
describing the database, not the table. Nothing in this project reads it.

**The repository is a partial subtree of the deployed application**, not a
buildable project. It contains `templates/`, `views/`, `schedule/`,
`pull_data/` and `README.md`. Absent, and never present in history
(`git log --all --diff-filter=D` returns nothing for any of them): `setup.py`,
any `.ini`, `requirements.txt`, `ecpick/__init__.py`, `ecpick/models/`,
`ecpick/csv_to_fasta.py`, `static/`.

Those files are not lost — the lab holds a local-server archive that carries
the full package root, including the `setup.py` and the `.ini` the GitHub
repository omits. So the answer to "does the build make sense" is that the
GitHub repository is a subset of a working Pyramid application, not a broken
project. That archive predates the `schedule/` code in the GitHub repository,
so it is a reference for structure, not the current tree.

## Confirmed from a copy of the production database

Read from a dump the lab provided, which supersedes the inferred figures
wherever the two disagree. The dump is not in this repository and must not be
committed, and neither are figures about the deployment it came from. What is
recorded below is what the export design depends on.

The deployed `enzymesdata` DDL matches `pull_ecpick_data.py` exactly and adds
what the script omits: `ENGINE=InnoDB`, `CHARSET=utf8mb4`,
`COLLATE=utf8mb4_0900_ai_ci`. **InnoDB satisfies the two-pass export's
transactional requirement**, and the absence of a primary key is confirmed at
the deployed level, not just in the statement that creates the table.

| `enzymesdata` | rows |
|---|---:|
| total | 561,510 |
| — `Swiss-Prot` | 302,500 |
| — `PDB` | 152,480 |
| — `KEGG` (excluded) | 87,441 |
| — `Test` (excluded) | 19,089 |
| **exported under the current source policy** | **454,980** |
| distinct sequences within that | 280,309 |

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

The "secured user accounts data" mentioned in the handover is an
administrative table, not a user base, and it never enters this project: the
export reads `enzymesdata` only, and the dump stays out of git and out of any
fixture.

**Item 6 stays blocked.** `execute_diamond` is imported from
`ecpick.csv_to_fasta` by `schedule/hitec_predictor.py:26`,
`schedule/exception_predictor.py:28` and
`schedule/exception_hitec_predictor.py:26`, and that module is one of the
absent ones. The DIAMOND parameters cannot be reconciled until it is available.

## First build against the real copy, 2026-08-07

Build `161bdee61cbb`. `enzymesdata` was loaded from the production dump into a
local MySQL 9.7.1 as `enzymex_real` — that table only; `user`, `job` and
`job_result` were deliberately not loaded — and exported through the
`enzymesdata_export` view in `docs/database.md`.

| | |
|---|---|
| view rows read | 404,340 |
| references exported | **278,573** (swissprot 234,839, pdb 43,734) |
| references carrying >1 EC | 23,184 |
| skipped: duplicate sequence | 123,300 |
| skipped: too short / too long | 2,298 / 9 |
| skipped: excessive ambiguity | 153 |
| skipped: looks like nucleotide | 7 |
| export | 395 s |
| `makeblastdb` | 11 s, 151 MB index |
| blastp, one 348-aa query | 2.4 s |
| phmmer, one 348-aa query | 7.8 s |

Two consecutive exports produced identical counts and the same FASTA digest,
so the build is deterministic at this scale. The profile layer was skipped on
this first build (`all --skip-profiles`); clustering 278,573 references has not
been timed yet and should not be assumed to fit the same budget.

Item 2 below is therefore discharged for BLAST and phmmer and still open for
the profile layer.

**A validation search behaves correctly.** Submitting P00330 (yeast alcohol
dehydrogenase 1) returns itself first from both methods at 100% identity and
100% coverage, `EC 1.1.1.1;1.1.1.54;1.1.1.78`, followed by the PDB structures
of the same protein and then ADH2. The three EC numbers on the top hit are the
view's grouping working as intended: they are three separate rows in
`enzymesdata`, and without the grouping the reference would have carried one of
them.

**An upstream data-quality problem worth reporting.** The 7 rows rejected as
nucleotide are genuine nucleic acid stored in the protein table, carrying EC
numbers inherited from the complex they were solved with:

```
1ASY_1  6.1.1.12  PDB  T-RNA (75-MER)  UCCGUGAUAGUUUAAUGGUCAGAAUGGGCGCUUGUC…
1GTR_1  6.1.1.18  PDB  RNA (74-MER)    GGGGUAUCGCCAAGCGGUAAGGCACCGGAUUCUGAU…
6KW4_5  3.6.4.12  PDB  DNA 167         CTAGTACTTCTCGACAAGCTTCAGGATGTATATATC…
```

Also `1ASZ_1`, `3DHS_1`, `3ZN8_3`, `5G2Y_1`. This export rejects them
compositionally, but ECPICK and the DIAMOND retrieval read the same table
without that check. It is EnzymeX's data to decide on — report it, do not
patch it here.

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

4. **Replace `app/web/` with EnzymeX views and templates.** *Written — see
   `enzymex/`.* Pyramid views and templates in ECPICK's own layout and markup.
   The lab applies one `config.include` line in `routes.py` and one `include`
   in `templates/ec/result.jinja2`; `views/ec.py` does not change. The three
   method tables stay deliberately separate (see the E-value comparability note
   in `docs/science.md`); that separation survives the port.

5. **Decide on execution mode.** *Decided: synchronous.* The existing queue
   exists because the deep models need a GPU and minutes. Against the real
   278,573-reference build blastp answers in about 2.4 s and phmmer in about
   7.8 s, so putting a search behind that queue would make it slower and add a
   table for nothing. What bounds the risk is the process-wide concurrency
   limit in `app/search/service.py`: an over-limit request is refused with
   "try again in a moment" rather than queued until memory runs out.
   `run_search` remains a plain function, so moving it into the scheduler later
   is a one-line change if EnzymeX would rather it lived there.

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

1. ~~Point `enzymex-refbuild inspect` at the real copy; verify its primary key,
   storage engine, source vocabulary, `UniprotID` mapping and representative
   PDB identifiers.~~ Done.
2. ~~Run a full build with `ENZYMEX_EXPORT_LIMIT` set, then unset. Compare the
   manifests.~~ Done — build `161bdee61cbb`, 278,573 references.
3. ~~Wire `run_search` into a scratch EnzymeX view and check hits resolve to the
   right EC and source.~~ Done — see the section below.
4. ~~Port the results markup into the EnzymeX result page alongside the model
   predictions.~~ Done — see "The buttons on the EC result page" below.
5. Decide DIAMOND's fate.
6. Move the build to whatever schedule the database refresh follows.

## The buttons on the EC result page, 2026-08-08

The lab asked for BLAST and HMMER as options underneath the three model
predictions, so a reader can compare sequence-comparison evidence against what
ECPICK, HIT-EC and CLEAN said, and for buttons rather than something that runs
by itself.

`_panel.jinja2` is included inside the per-sequence loop of
`templates/ec/result.jinja2` and renders one button per available method.
Clicking one posts the sequence to `/search/sequence.do`, which runs that method
alone and returns the hit table as an HTML fragment.

Design points, and why:

| Decision | Reason |
| --- | --- |
| One method per request | Both together take ~15 s. Nobody who opened the page to read a prediction should wait for a search they did not ask for. |
| Nothing runs on page load | Same reason. The panel starts empty and says so. |
| The endpoint returns HTML, not JSON | `_method.jinja2` is the only definition of a hit table, included by both the standalone result page and the fragment. The JavaScript formats nothing, so the two cannot drift. |
| Config via `request.sequence_search` | Registered by `includeme`, so `views/ec.py` keeps its own return dict and the diff to code we do not own stays at two lines. |
| Only available methods get a button | A button whose only function is to report its own unavailability is worse than no button. With no build at all the panel renders nothing and the page is unchanged. |
| The endpoint requires a login | The page it sits on redirects anonymous visitors to the login. The standalone `/search` page stays open, as it writes nothing. |

Verified against ECPICK's real `layout.jinja2` and build `161bdee61cbb`
(278,573 references), through WSGI:

| Check | Result |
| --- | --- |
| Panel inside the real layout | 200, 19,744 bytes, buttons for blastp and phmmer only |
| Runs anything on page load | No |
| blastp fragment | 200, 25 hits, top `EXRP00330` EC `1.1.1.1;1.1.1.54;1.1.1.78`, 3.4 s |
| phmmer fragment | 200, 25 hits, same top hit, 12.5 s |
| Fragment is not a page | No `<html>` in either |
| Custom parameters (PAM70 / 5 hits / 1e-30 / 80% coverage) | 200, 5 hits, all three echoed back |
| `matrix=BLOSUM62 -out /tmp/pwned2` | 400, refused, no file created |
| `method=; rm -rf /` | 400, refused |
| Newline in the record label | Cannot open a second FASTA record |

hmmscan has no button because the profile layer is not built on this machine;
`reference_status` reports it unavailable and the panel omits it.

## The EnzymeX view, 2026-08-07

`enzymex/` holds the drop-in: `views/search.py` and `templates/search/`, in
ECPICK's layout and markup, with an install note and the three route lines.

It was verified rather than eyeballed. `tests/test_enzymex_view.py` performs
exactly the install the README describes into a throwaway `ecpick` package,
scans the `@view_config` decorators the way Pyramid does, and drives the pages
through WSGI. Separately it was rendered inside EnzymeX's real
`layout.jinja2` — read from the clone, never written to — and run end to end
against build `161bdee61cbb`:

| | result |
| --- | --- |
| `GET /search` in the real layout | 200, 21,524 bytes |
| `POST /search.do`, P00330, blastp | 302 to `/search/result?job=…` |
| result page | 200, 25 ranked hits |
| top hit | `EXRP00330`, EC 1.1.1.1;1.1.1.54;1.1.1.78 |
| `matrix=BLOSUM62 -out /tmp/pwned` | rejected on the form; no file written |

Three things the lab has to decide are listed at the end of `enzymex/README.md`.

## Where `routes.py` comes from, 2026-08-10

`ecpick/routes.py` is not in the `datax-lab/enzymex` repository — it holds
`views/`, `templates/`, `pull_data/` and `schedule/` only. The lab's answer is
the local-server archive, which does carry `routes.py`, `__init__.py`,
`models/`, `static/` and the `.ini`. Its `includeme(config)` is a flat list of
`config.add_route` calls, so the install line goes in unchanged.

The archive is not the deployed tree, though, and the gap is wide enough to
matter:

| | archive | `datax-lab/enzymex` |
|---|---|---|
| `templates/ec/result.jinja2` | 101 lines, one prediction column | 617 lines, ECPICK / HIT-EC / CLEAN |
| routes defined | 23 | 42 referenced by `views/` |

Nineteen routes the current views call are absent from the archive's
`routes.py`, among them `ref_data_info`, `job_status`, `queue_data`, `members`,
`news` and `version_history`. The panel include targets the 617-line template,
so the archive is scaffolding for standing a copy up locally, not the file to
patch. The one-line change still has to be applied to whatever `routes.py` the
server actually runs.

### Search parameters

The lab asked for neutral defaults with the choice left to the user, and both
halves are now implemented in `app/search/params.py`. The form exposes E-value,
hits per query, minimum query coverage, scoring matrix, composition-based
statistics and gap costs; each field is pre-filled with the tool's own default
and labelled as such, and the panel is collapsed, so the run you get without
touching anything is the neutral one.

Every value is coerced, bounded and — for matrix and `comp_based_stats` —
whitelisted before it reaches a command line. Gap costs are the exception worth
naming: BLAST accepts them only in combinations tabulated per matrix, so they
are bounded to sane integers and BLAST is left as the authority on the pairing,
with its complaint translated into a message the submitter can act on. A table
copied into this repository would be one more thing to keep in sync with NCBI.

The coverage filter is not a BLAST option; it drops hits after parsing and is
recorded in a note on the result page when used. It exists because of the Agave
cross-check in `docs/external-validation.md`, where 30% of annotations rested on
an alignment covering less than half the query. It is off unless asked for,
because a floor is still nobody's decision.

Whatever ran is recorded on the result as `search_parameters` and shown on the
page, so a hit list can be reproduced from the record rather than from whatever
the server defaults happen to be that month.

## Known limitations to carry across

* Profile HMMs cover only QC-passing clusters, never the whole reference set.
  The page must keep saying so.
* `phmmer` is the slowest method and scales with reference count. It is the
  first thing to hit a timeout on a large copy.
* There is no authentication, rate limiting or CSRF protection here; EnzymeX
  presumably has its own, and this code assumes it.
* Job results are files on disk with a 24-hour retention, which is a test
  server's answer, not a production one.
