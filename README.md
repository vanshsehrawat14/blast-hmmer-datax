# blast-hmmer-datax

A standalone test server that adds **BLAST and HMMER protein-sequence
comparison** to an EnzymeX-style results page, searching references exported
from a *copy* of the EnzymeX `enzymesdata` table.

> **This repository is an independent test environment. It is not the official
> EnzymeX production codebase and does not modify the live EnzymeX service.**
> Nothing here has been deployed to, or pushed at, the EnzymeX repository.
> Once reviewed, the modules under `app/search/` and `app/references/` are
> intended to be lifted into EnzymeX; see [`docs/integration.md`](docs/integration.md).

Part of a UNLV independent study.

## What it does

```
FASTA submission ─► validate + isolate ─► blastp ─┐
                                       ─► phmmer ─┼─► parse + normalize ─► results page
                                       ─► hmmscan ┘         │
                                                            └─ EC / source / description
                                                               from the copied database
```

Results are presented as **sequence-comparison evidence**, not EC predictions.
ECPICK, HIT-EC and CLEAN are not run here; the page says where they would sit.

## Quick start

```bash
bash scripts/00_setup.sh          # micromamba + BLAST+/HMMER/MAFFT/MMseqs2
cp .env.example .env              # copied-database credentials
$EDITOR .env                      # set ENZYMEX_DB_CONFIRM_COPY=true

make inspect                      # read-only look at the copied enzymesdata
make refbuild                     # export -> BLAST db -> profile HMMs -> manifest
make serve                        # http://127.0.0.1:8000
make test
```

Docker Compose and systemd paths are in [`docs/deployment.md`](docs/deployment.md).

| command | does |
|---|---|
| `make inspect` | read-only report on the copied `enzymesdata` table |
| `make refbuild` | full offline reference build |
| `make status` | what is currently built |
| `make serve` / `make serve-prod` | development / production-style server |
| `make health` | pretty-print `/health` |
| `make test` / `make test-unit` | full suite / no external tools or database |
| `make validate-external` | reproduce the external BLAST fold and benchmark a leakage-safe EnzymeX build |
| `make clean` | delete everything under `var/` |
| `make poc-all` | the original EC 1.1.1.1 comparison (below) |

## The two halves

**Offline** (`enzymex-refbuild`, minutes) is the only phase that reads the
copied MySQL database. It validates and deduplicates the sequences, then writes
a FASTA, a SQLite
metadata store, a BLAST database, a pressed profile-HMM database and a build
manifest into `var/reference/`.

The default export accepts only normalized `swissprot` and `pdb` rows. All
three search paths use that same export. Exact cross-source duplicates are
collapsed with the Swiss-Prot row preferred as canonical metadata and the PDB
row retained as provenance; fields are not merged across rows.

**Online** (the web app, seconds) never opens a database connection. Hit
metadata comes from the SQLite file written by the build, so a hit and its
annotation always come from the same versioned build, and a misconfigured
credential is unreachable from a user request.

Full picture: [`docs/architecture.md`](docs/architecture.md).

## The HMMER decision

`phmmer` covers **every** exported reference. Profile HMMs cover only the
subset that clusters into families passing quality control, and the page says
so rather than letting an absent profile hit read as evidence of absence.

Profiles are **not** built per EC number. An EC number names a reaction, not a
family, and enzymes catalysing the same reaction are frequently unrelated in
sequence. Clustering the development reference set by similarity and then
reading off the EC gives 13 distinct profiles for glutathione transferase
(2.5.1.18), 4 for carbonic anhydrase (4.2.1.1, the α/β/γ classes) and 5 for
superoxide dismutase (1.15.1.1, the Cu/Zn, Mn/Fe and Ni enzymes). A single
per-EC profile would have merged unrelated folds into a model of nothing.

So the pipeline clusters first (MMseqs2, 35% identity / 80% bidirectional
coverage), gates each cluster on size, length consistency, alignment quality
and profile length, and attaches EC annotation to the surviving families
afterwards, reporting `ec_purity` per family instead of assuming it.

`hmmscan` is used rather than the faster `hmmsearch` because `hmmsearch`
computes E-values against the *submitted* set, which would make the same
protein score differently depending on how many others were pasted alongside
it. Reasoning and citations: [`docs/science.md`](docs/science.md).

## Reading the results

Three tables, never side by side, because the numbers are not the same
quantity:

* **E-values are not comparable across methods.** BLAST and phmmer score
  against the reference set under different statistical models; hmmscan scores
  against the profile set. Bit scores are the stable thing to compare within a
  method.
* **Percent identity is BLAST only.** HMMER's tabular output does not report
  it, so the column stays empty rather than being filled with something
  identity-shaped.
* **Identity without coverage is meaningless.** Query coverage below 50% is
  greyed out.
* **No-hit is not "not an enzyme."** It means this reference set has no
  detectable relative.

## Measured behaviour

Source-policy development build `32abd580b689`: 2,677 fixture rows → 1,574
Swiss-Prot/PDB-labelled references (1,387 Swiss-Prot, 187 PDB), 201 clusters
→ 47 profiles covering 85.9% of references. WSL2 / Ubuntu 24.04, 2 search
threads.

| one-query check | blastp | phmmer | hmmscan | all three |
|---|---:|---:|---:|---:|
| human SOD2 positive | 0.222 s | 0.278 s | 0.174 s | 0.925 s |
| unrelated GFP | 0.224 s | 0.228 s | 0.172 s | 0.850 s |

The full build took 137.1 s, including 133.9 s for clustering and the profile
layer. Scaling notes: [`docs/reference-build.md`](docs/reference-build.md).

These are measurements of this build, not performance claims about the tools.
The fixture's `pdb` values are test labels on reviewed Swiss-Prot sequences,
not genuine PDB-derived records.

## The public Swiss-Prot/PDB build

The same pipeline was then run against real data. `scripts/load_swissprot_pdb.py`
loads the public `SwissProt_PDB_2022` set from the `datax-lab/HIT-EC` repository
into a separate `enzymex_real` database, joined against `datax-lab/IDF-EC` for EC
annotation. It is a proxy for the copied EnzymeX table, not the table itself, but
its PDB rows are genuine provenance rather than synthetic labels.

Build `56b491bee73d`: 273,500 rows → 272,112 references (231,577 Swiss-Prot,
40,535 PDB), 235,821 carrying an EC, 1,388 skipped (1,256 too short, 124
excessively ambiguous, 8 too long). Sequence length 30 to 8,903, mean 414.0.
Built in 268.7 s total, 252.2 s of it the export and 13.4 s `makeblastdb`.
Profiles were skipped on this generation, so every `hmmscan` figure in this
repository still comes from build `32abd580b689`.

| one-query check | blastp | phmmer |
|---|---:|---:|
| human SOD2 | 1.797 s | 7.132 s |
| unrelated GFP | 1.579 s | 8.307 s |

No search code changed. The only new file is the loader.

**GFP is not a clean negative at this scale.** Against 1,574 fixture references
it returned no hits from any method; against 272,112 it returns 25 blastp hits,
the top at 97.5% identity and E 8.4e-175. All 25 are PDB fusion constructs where
GFP is joined to an unrelated protein, and the 15 of them that carry an EC carry
eight different ones, none belonging to GFP.

The results page already exposes this without leaving the page: the top hit
covers 100% of the query but only 54.6% of the subject, and across all 25 hits
query coverage is 0.97 to 1.00 while subject coverage is 0.27 to 0.84. That
asymmetry is the fusion signature, and it is why subject coverage is computed
here by merging HSP intervals rather than omitted because BLAST+ has no `scovs`
field.

Note also that human SOD2 is present in this set, so the held-out positive demo
in `data/demo/` only behaves as a holdout against the fixture build.

## Documentation

| | |
|---|---|
| [HANDOFF.md](HANDOFF.md) | verified setup, build and test figures, demo inputs and expected output, transfer plan |
| [pipeline.md](docs/pipeline.md) | end to end walkthrough: where the copy enters, what each method does, what is shown |
| [architecture.md](docs/architecture.md) | module layout, framework choice, execution model |
| [science.md](docs/science.md) | what each method measures, the HMMER design, citations |
| [database.md](docs/database.md) | copied-database setup, schema discovery, data quality |
| [reference-build.md](docs/reference-build.md) | build artifacts, determinism, scaling |
| [deployment.md](docs/deployment.md) | Docker Compose, systemd, health endpoint |
| [configuration.md](docs/configuration.md) | every environment variable |
| [security.md](docs/security.md) | subprocess safety, input validation, secrets |
| [testing.md](docs/testing.md) | suite layout, manual procedure, runtime figures |
| [external-validation.md](docs/external-validation.md) | supplied fold reproduction, leakage controls and benchmark cohorts |
| [integration.md](docs/integration.md) | what to change when EnzymeX access is granted |
| [setup_notes.md](docs/setup_notes.md) | environment notes from the proof of concept |
| [ATTRIBUTION.md](ATTRIBUTION.md) | tool versions, exact commands, citations, licences |

## Limitations

* Profile HMMs cover a QC-passing subset, never the whole reference set.
* The 272,112-reference public build has no profile layer, so `hmmscan` figures
  come only from the 1,574-reference fixture build.
* `phmmer` is the slowest method and scales with reference count; it is the
  first thing that will need its timeout raised on a large copy.
* Searches run synchronously. Fine at the scale measured above; the service
  layer is structured so a worker queue is a one-function change.
* No authentication, no per-client rate limiting. Internal test server.
* Job results are files with a 24-hour retention.
* The public `datax-lab/enzymex` repository was not reachable while this was
  built, so the eventual UI and schema compatibility work is based on the
  documented column list and the live site, and must be re-verified against
  the real codebase.

## The proof of concept

The original experiment (BLAST, HMMER and DIAMOND over 27 curated EC 1.1.1.1
sequences from UniProt, normalized into one schema) is still here and still
runs (`make poc-all`, output in
[`results/comparison/comparison_report.md`](results/comparison/comparison_report.md)).

It was kept for two reasons: its committed 27-sequence dataset is the offline
fixture the end-to-end test builds a real BLAST database and profile HMM from,
and its DIAMOND comparison is the evidence behind the recommendation in
`docs/integration.md` about how DIAMOND and BLAST should be presented together
on the EnzymeX page. Its scripts live under `scripts/0*.sh` and its Makefile
targets are prefixed `poc-`.
