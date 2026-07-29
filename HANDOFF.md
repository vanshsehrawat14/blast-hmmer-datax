# Handoff

Final state of the standalone BLAST/HMMER test server, verified end to end on
2026-07-29. Everything below was re-run from a clean `var/` on this commit.

> Independent test environment. Not the official EnzymeX codebase, nothing was
> pushed at the EnzymeX repository, and no EnzymeX service was touched.

Audited: fresh-clone setup, the full test suite, a reference rebuild from a
copied MySQL database, a positive and a negative query through the browser, and
whether the numbers on the results page actually belong to the reference record
they are printed next to. Findings and fixes are at the end.

---

## 1. Setup

Ubuntu 24.04 (WSL2 here). Needs `micromamba`/`mamba`/`conda` on PATH; the
setup script installs micromamba to `~/.local/bin` if none is found.

```bash
git clone <repo> blast-hmmer-datax && cd blast-hmmer-datax
bash scripts/00_setup.sh          # micromamba + BLAST+ / HMMER / MAFFT / MMseqs2
cp .env.example .env
$EDITOR .env                      # DB credentials, ENZYMEX_DB_CONFIRM_COPY=true
```

`make` runs every recipe inside the `blast-hmmer-datax` environment, so no
`activate` step is needed. Verify the toolchain:

```bash
make versions
```

Recorded here: blastp/makeblastdb 2.16.0+, HMMER 3.4, MAFFT v7.526,
MMseqs2 18.8cc5c, Python 3.12.13, DIAMOND 2.2.4 (optional, proof of concept only).

### Copied database

Production is never a valid target. `ENZYMEX_DB_CONFIRM_COPY=true` is required
before the build will open a connection.

```sql
CREATE USER 'enzymex_ro'@'%' IDENTIFIED BY '<password>';
GRANT SELECT ON enzymex_copy.* TO 'enzymex_ro'@'%';
```

Without EnzymeX access there is no copy, so a local stand-in with the same
schema is used. Full commands are in
[`docs/database.md`](docs/database.md#local-fixture-database-development-only);
in short:

```bash
micromamba create -y -n mysql-fixture -c conda-forge mysql-server
# initialise + start mysqld on :3307, create enzymex_copy and enzymex_ro
python scripts/dev_seed_fixture.py --port 3307 --user root --password ""
```

That loads 2,677 rows from reviewed UniProtKB entries across twelve EC numbers,
plus 10 deliberately damaged rows, and writes a 111-sequence holdout to
`var/fixture/holdout_queries.fasta` that is excluded from the table.

## 2. Reference build

```bash
make inspect     # read-only characterisation of the copy, writes nothing
make refbuild    # export -> BLAST db -> cluster -> profile HMMs -> manifest
make status      # what is currently built
```

Individual stages: `make export`, `make blast`, `make hmmer`. To skip the
profile layer on a first large build: `enzymex-refbuild all --skip-profiles`
(BLAST and phmmer still cover every reference).

## 3. Running the server

```bash
make serve                        # 127.0.0.1:8000, reload
make serve-prod                   # 0.0.0.0:8000, 2 workers, no reload
make health                       # pretty-printed /health
```

Docker Compose and systemd are in [`docs/deployment.md`](docs/deployment.md):

```bash
docker compose -f deploy/docker-compose.yml up -d
sudo systemctl enable --now enzymex-blast-hmmer
```

`/health` returns `ok`, `degraded` (artifacts fine, copied database
unreachable) or `unavailable` (503). It never contains the password.

## 4. Build statistics

Build `261967e8d173`, 2026-07-28T16:21:51Z, 201.2 s total on WSL2 / Ubuntu
24.04, 4 build threads.

| | |
|---|---|
| rows read from `enzymesdata` | 2,677 |
| exported references | 2,380 |
| skipped | 297 |
| duplicate sequences merged (kept as provenance) | 244 |
| other skips | 47 too_short, 2 excessive_ambiguity, 1 each null / empty / internal_stop / looks_like_nucleotide |
| sequence length | 30 to 1,861, mean 359.7 |
| with EC annotation | 2,380 |
| multi-EC records | 205 |
| sources | swissprot 1,340, trembl 710, pdb 188, kegg 142 |

Sources other than Swiss-Prot are the fixture's simulated provenance labels;
every sequence in it is genuinely Swiss-Prot. On a real copy this column is the
copy's own.

Profile layer:

| | |
|---|---|
| clusters (MMseqs2, 35% id / 80% bidirectional coverage) | 242 |
| accepted | 64 |
| rejected | 178, all `too_few_members` |
| profiles built | 64 (0 failed alignment or profile QC) |
| references covered by a profile | 2,113 of 2,380 (88.8%) |
| distinct consensus EC | 15 |
| mean EC purity | 1.00 |
| match states | 143 to 917 |
| mean pairwise identity | median 0.558, min 0.375 |

Stage timings: export 1.0 s, `makeblastdb` 0.9 s, cluster + MAFFT + hmmbuild +
hmmpress 195.6 s. The profile layer is ~97% of the build.

**One EC gives several profiles, as intended.** Glutathione transferase
(2.5.1.18) produces 14, carbonic anhydrase (4.2.1.1) 10, superoxide dismutase
(1.15.1.1) 6. The six SOD profiles split by fold, not by EC: EXF00003 (Mn/Fe),
EXF00033 (Mn), EXF00057 (Fe) against EXF00005, EXF00030, EXF00035 (all Cu/Zn).

## 5. Test results

```bash
make test        # everything available
make test-unit   # no external tools, no database
make test-db     # needs ENZYMEX_DB_* pointing at a copy
```

| run | result |
|---|---|
| `make test`, no DB credentials exported | **157 passed, 13 skipped** (20.7 s) |
| `make test`, credentials exported | **170 passed, 0 skipped** (20.9 s) |

The 13 skips are the `mysql`-marked tests in `tests/test_database.py`; they run
only when `ENZYMEX_DB_PASSWORD` is in the environment. `tests/test_e2e.py`
builds a real BLAST database and a real profile HMM from the committed
27-sequence EC 1.1.1.1 dataset, so `make test` exercises the actual tools.

## 6. Demo inputs

Committed under `data/demo/`. Both are absent from the reference set, so
neither can match itself.

**Positive** `data/demo/positive_sod2_human.fasta`: UniProt P04179, human
Superoxide dismutase [Mn], mitochondrial, 222 aa, EC 1.15.1.1. Chosen because
it is in the holdout set and because 1.15.1.1 is served by unrelated folds, so
it tests whether clustering separated them.

**Negative** `data/demo/negative_gfp.fasta`: UniProt P42212, *Aequorea
victoria* green fluorescent protein, 238 aa. Not an enzyme and unrelated to any
of the twelve reference EC families.

The proof-of-concept `data/raw/queries_negative.fasta` is an alpha-amylase. It
is negative against the 27-sequence EC 1.1.1.1 set only; against this reference
set it is a strong positive, which is why the demo does not use it.

## 7. Expected demo output

**Positive**, ~1.5 s, all three methods:

* `blastp` 25 hits. Top: `EXR2306`, Q8HXP6 Superoxide dismutase [Mn]
  mitochondrial, EC 1.15.1.1, swissprot, 99.1% identity, 222 aa alignment,
  100% query and subject coverage, E 1.2e-168, bit score 459. Every one of the
  25 is EC 1.15.1.1.
* `phmmer` 25 hits, same top four references in the same order, no identity
  column, query region 1–222.
* `hmmscan` **exactly 3 profiles**: EXF00003 (135 members, E 1.4e-92),
  EXF00033 (13 members, E 1.1e-75), EXF00057 (5 members, E 2.4e-50). All
  consensus EC 1.15.1.1, all 100% EC purity. **No Cu/Zn profile appears**,
  which is the correct result for a Mn-SOD query.

**Negative**, ~1.4 s: "No hits" from all three methods, each quoting its own
threshold (E ≤ 1e-03), with the page stating that this means no detectable
relative in the reference set rather than "not an enzyme".

A useful detail visible in the positive run: `EXR2307` shows 100.0% identity but
only 89% query coverage, because that reference is a 198-aa fragment. It is the
reason the page pairs identity with coverage and greys out coverage below 50%.

## 8. Screenshots

| | |
|---|---|
| ![submission](docs/screenshots/01-submission-page.jpg) | `docs/screenshots/01-submission-page.jpg`: submission page with the positive query pasted |
| ![results](docs/screenshots/02-results-header.jpg) | `02-results-header.jpg`: result header, job/build ids, CSV and JSON links, reading guidance |
| ![blastp](docs/screenshots/03-results-blastp.jpg) | `03-results-blastp.jpg`: BLAST table |
| ![hmmscan](docs/screenshots/04-results-hmmscan.jpg) | `04-results-hmmscan.jpg`: profile HMM table |
| ![negative](docs/screenshots/05-results-negative.jpg) | `05-results-negative.jpg`: negative query, no hits from any method |

## 9. Verification performed

Beyond the automated suite, this handoff checked that the displayed values
belong to the record they are printed against. A clean pipeline with a wrong
mapping would pass every unit test.

1. **Every reference row against its source row.** All 2,380 `reference` rows
   in `metadata.sqlite3` re-checked against `enzymesdata` via `source_pk`:
   identifier derivation, normalised EC, normalised source, sequence SHA-256
   and length. **0 mismatches.**
2. **FASTA against metadata.** All 2,380 deflines and sequences in
   `references.fasta` checked against SQLite. **0 mismatches.** This is what
   makes `sseqid` a safe lookup key: `makeblastdb` runs without `-parse_seqids`,
   so BLAST returns the identifier byte-for-byte.
3. **Displayed numbers against raw tool output.** `blastp` re-run standalone
   for the positive query. Identity, alignment length, E-value, bit score and
   coverage on the page match the raw table exactly for all four top hits
   (e.g. `EXR2306` 99.099 / 1.17e-168 / 459 / qcovs 100).
4. **Top hits traced to MySQL.** `EXR2306`→id 2306 Q8HXP6, `EXR2304`→2304
   Q8HXP3, `EXR1966`→1966 Q5FB30, `EXR2630`→2630 Q9XS41. EC and source on the
   page match the row in every case.
5. **Profile identity.** The Mn-SOD query hit the three Mn/Fe profiles and none
   of the three Cu/Zn profiles. A per-EC profile would have merged all six.
6. **HMMER coordinate normalisation.** `phmmer` reports query regions on the
   *submitted* sequence: the 198-aa reference `EXR2307` shows region 25–222 and
   89% query coverage, consistent with 198/222. The role flip between phmmer
   (query becomes the profile) and hmmscan (profile is the target) is handled
   in `app/search/parsers.py` and is correct in both directions.
7. **Example sequence.** The placeholder FASTA on the submission page is
   genuinely UniProt P00330 (yeast ADH1), byte-identical to UniProt and to the
   test fixture.
8. **Secrets and artifacts.** `git ls-files` shows no `.env`, no `var/`, no
   BLAST/DIAMOND index and no `.hmm`. `/health` reports
   `enzymex_ro@127.0.0.1:3307/enzymex_copy` and never the password.

## External fold validation (2026-07-29)

The supplied Swiss-Prot fold was also validated independently. The split has
no accession or exact-sequence overlap, and BLAST 2.16 selected the same raw
top subject as the shared BLAST+ 2.5.0 output for all 452 sampled queries with
hits in both runs. For the copied development EnzymeX build, removing all 241
references matching a test sequence left 2,139 references. On the 373
truth-selected queries covered by those reference ECs, BLAST and phmmer had
82.57% and 82.04% top-hit EC overlap, while taking 10.1 s and 133.3 s. hmmscan
reached 98.57% only on the much narrower 279-query profile-covered slice.

This supports BLAST as the default sequence-evidence search, with profile HMMs
as optional family evidence. It does not support adding phmmer to every job
for nearly identical retrieval on this data. The full method, scope limits,
raw-artifact hashes and results are in
[`results/validation/fold_0_report.md`](results/validation/fold_0_report.md).

## 10. Known limitations

* Profile HMMs cover a QC-passing subset (88.8% here), never the whole
  reference set. The results page says so on every profile result.
* 178 of 242 clusters were rejected for having fewer than 5 members. On a
  larger copy that ratio will change; check `skipped_clusters.tsv` and
  `profile_member_coverage` before trusting the layer.
* `phmmer` is the slowest method and scales with reference count. First thing
  to need a raised timeout on a large copy.
* The build is ~97% MAFFT + `hmmbuild`, run serially. Embarrassingly parallel
  if it ever matters.
* Searches run synchronously behind a 2-slot semaphore; excess submissions get
  503 rather than queueing.
* No authentication, no rate limiting, no CSRF protection. Internal test server.
* Job results are files with 24-hour retention.
* E-values are not comparable across the three tables. The page repeats this;
  any port must keep doing so.
* The fixture's `source` labels are simulated. Real provenance comes from the
  real copy.
* `datax-lab/enzymex` was unreachable while this was built, so all UI and
  schema compatibility work is based on the documented column list and the live
  site and must be re-verified against the real codebase.
* `live_settings` in `tests/conftest.py` is currently unused. Left in place
  rather than removed as part of this audit.

## 11. Modules to transfer into EnzymeX

Portable as-is. None of these import FastAPI, Jinja2 or Starlette.

```
app/references/     export.py, db.py, metadata.py, blast_build.py,
                    cluster.py, hmmer_build.py, manifest.py, cli.py
app/search/         service.py, blast.py, hmmer.py, parsers.py,
                    outcome.py, subprocess_utils.py
app/fasta.py        validation and normalisation, both sides
app/schemas.py      the normalized result model
app/jobs.py         job directories, persistence, retention
app/config.py       merge into EnzymeX settings, do not copy wholesale
```

Dependency edge runs one way:

```
app/web/  ──►  app/search/  ──►  app/references/  ──►  app/config.py
 (drop)          (keep)             (keep)              (merge)
```

`app/web/` is **not** for transfer. The results page is a reference for *what
to show*, not markup to lift. `parsers.py` is the easiest piece to move: pure
functions over text, no subprocess, no filesystem, no database.

Host application call:

```python
from app.fasta import parse_submission
from app.search.service import run_search

records = parse_submission(fasta_text, max_sequences=10, max_length=5000)
result  = run_search(settings, records, ["blastp", "phmmer", "hmmscan"])
# result.flat_rows() is the tabular form
```

## 12. First steps once EnzymeX access is granted

1. **`enzymex-refbuild inspect` against the real copy, before anything else.**
   The column alias table in `app/references/db.py` was written against the
   documented column list and verified only against the fixture. If a column
   resolves to the wrong name, that table is the only thing to edit. If the
   real table has no primary key, export from a view that supplies one.
2. **Full build, then read the manifest, not the logs.** Check
   `skipped.tsv`, `skipped_clusters.tsv` and `profile_member_coverage`. Expect
   `ENZYMEX_PROFILE_MIN_MEMBERS` to need raising if the accepted-cluster count
   makes the MAFFT loop too slow. Every runtime figure in these docs comes from
   2,380 references; none of them transfer.
3. **Wire `run_search` into a scratch view and re-run check 9.4 above** on real
   data: pick hits and trace them back to `enzymesdata` rows by `source_pk`.
   Do this before any UI work.
4. **Decide execution mode.** EnzymeX already schedules ECPICK, HIT-EC and
   CLEAN. If these results are to share a page, run `run_search` as another
   step in that job rather than in the request.
5. **Reconcile with DIAMOND.** EnzymeX already uses it for similar-protein
   retrieval. DIAMOND and blastp answer the same question at different
   sensitivity, so either present both with the difference labelled or drop
   one. `results/comparison/comparison_report.md` quantifies it: DIAMOND's
   default mode missed the most distant positive that `--very-sensitive`,
   BLAST and HMMER all found.
6. **Settle identifier policy.** References are `EXR<enzymesdata.id>`. To
   surface UniProt accessions instead, the mapping is already in
   `metadata.sqlite3` (`source_pk`, `description`). Change the display, not the
   internal identifier: the internal one is what keeps deflines safe for the
   tools.
7. **Port the markup last**, keeping the three tables separate.

Longer form in [`docs/integration.md`](docs/integration.md).

## 13. Changes made during this audit

* Documented the local fixture MySQL setup in `docs/database.md`. It was the
  one step with no written procedure, which made a rebuild unreproducible from
  a fresh clone.
* Fixed test isolation: `Settings(_env_file=None, ...)` still reads the process
  environment, so running the suite in a shell configured for the copied
  database (the documented way to run `-m mysql`) leaked `ENZYMEX_*` into every
  other test. `test_dsn_summary_never_contains_the_password` failed outright
  that way. Added an autouse fixture in `tests/conftest.py` that strips the
  prefix from non-`mysql` tests, and pinned host/port in that assertion.
* Added `data/demo/` with the positive and negative inputs above.
* Rewrote user-facing copy on all five templates and the submission error
  messages, and removed em dashes and padding from `README.md` and `docs/`.
* Marked `*.jpg`/`*.png` binary in `.gitattributes` for the screenshots.

No functional change to the search pipeline, the parsers, the reference build
or the result model. Nothing in the audit found a wrong value on the page.
