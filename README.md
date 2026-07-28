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
| `make clean` | delete everything under `var/` |
| `make poc-all` | the original EC 1.1.1.1 comparison (below) |

## The two halves

**Offline** (`enzymex-refbuild`, minutes) reads the copied MySQL database once,
validates and deduplicates the sequences, and writes a FASTA, a SQLite
metadata store, a BLAST database, a pressed profile-HMM database and a build
manifest into `var/reference/`.

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
reading off the EC gives 14 distinct profiles for glutathione transferase
(2.5.1.18), 10 for carbonic anhydrase (4.2.1.1, the α/β/γ classes) and 6 for
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

Development build: 2,677 rows → 2,380 references, 242 clusters → 64 profiles
covering 89% of references. WSL2 / Ubuntu 24.04, 2 search threads.

| queries | methods | wall clock |
|---|---|---|
| 1 | blastp | 0.30 s |
| 1 | phmmer | 1.16 s |
| 1 | hmmscan | 0.27 s |
| 10 | all three | 9.01 s |

Peak resident memory 55 MB (web process) / 83 MB (children). Full build: 117 s,
of which ~115 s is MAFFT + `hmmbuild` across 64 families. Scaling notes:
[`docs/reference-build.md`](docs/reference-build.md).

These are measurements of this build, not performance claims about the tools.

## Documentation

| | |
|---|---|
| [HANDOFF.md](HANDOFF.md) | verified setup, build and test figures, demo inputs and expected output, transfer plan |
| [architecture.md](docs/architecture.md) | module layout, framework choice, execution model |
| [science.md](docs/science.md) | what each method measures, the HMMER design, citations |
| [database.md](docs/database.md) | copied-database setup, schema discovery, data quality |
| [reference-build.md](docs/reference-build.md) | build artifacts, determinism, scaling |
| [deployment.md](docs/deployment.md) | Docker Compose, systemd, health endpoint |
| [configuration.md](docs/configuration.md) | every environment variable |
| [security.md](docs/security.md) | subprocess safety, input validation, secrets |
| [testing.md](docs/testing.md) | suite layout, manual procedure, runtime figures |
| [integration.md](docs/integration.md) | what to change when EnzymeX access is granted |
| [setup_notes.md](docs/setup_notes.md) | environment notes from the proof of concept |
| [ATTRIBUTION.md](ATTRIBUTION.md) | tool versions, exact commands, citations, licences |

## Limitations

* Profile HMMs cover a QC-passing subset, never the whole reference set.
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
