# Architecture

> This repository is an independent test environment. It is not the official
> EnzymeX production codebase and does not modify the live EnzymeX service.

## Two halves that never run at the same time

```
OFFLINE (operator, minutes)                ONLINE (user request, seconds)
───────────────────────────                ──────────────────────────────
copied enzymesdata (MySQL, read-only)      FASTA submission
        │                                          │
   validate / dedup / normalize             validate, assign Q1..Qn
        │                                          │
   references.fasta ─┬─ makeblastdb ─────► BLAST database ──► blastp
                     │                                          │
                     ├─ (searched directly) ─────────────► phmmer
                     │                                          │
                     └─ mmseqs cluster ─► QC ─► mafft ─►  hmmbuild ─► hmmpress
                                                                │
                                                          profiles.hmm ──► hmmscan
        │                                          │
   metadata.sqlite3 ◄─────── hit → EC / source / description
        │                                          │
   build_manifest.json ──► reference_build_id ──► results page + CSV/JSON
```

The copied MySQL database is read only during an offline `enzymex-refbuild`.
The exporter makes two scans through one connection and repeatable snapshot.
A user request never opens a database connection: hit metadata comes from the
SQLite file written alongside the search indexes.
That makes a misconfigured credential impossible to reach from the request
path, and it guarantees that a hit and its annotation always come from the
same build.

## Layout

```
app/
  config.py              all settings, one place, env-driven
  fasta.py               parsing + validation, shared by both halves
  schemas.py             the normalized result model
  jobs.py                job ids, isolated directories, retention
  references/            OFFLINE only
    db.py                read-only MySQL, schema discovery
    export.py            enzymesdata -> validated FASTA + metadata
    metadata.py          the SQLite store (write and read sides)
    blast_build.py       makeblastdb
    cluster.py           MMseqs2 clustering + QC gates
    hmmer_build.py       MAFFT -> hmmbuild -> hmmpress, profile QC
    manifest.py          build id, checksums, tool versions
    cli.py               enzymex-refbuild
  search/                ONLINE
    service.py           orchestration; the integration entry point
    blast.py             blastp runner
    hmmer.py             phmmer and hmmscan runners
    parsers.py           pure text -> RawHit
    subprocess_utils.py  the only place a process is started
    outcome.py           per-method result carrier
  web/                   FastAPI + Jinja2, thin
deploy/                  Dockerfile, compose, systemd unit
scripts/                 proof-of-concept pipeline + dev fixture loader
tests/
```

Dependency direction is one-way: `web` → `search` → `references` → `config`.
Nothing under `search/` or `references/` imports FastAPI, and nothing under
`search/` imports `references.db`. `app/search/service.py` is the boundary a
host application calls.

## Framework choice

FastAPI with server-rendered Jinja2 templates.

* The normalized result model is a Pydantic model regardless, because it has
  to serialise to JSON and validate on reload. FastAPI makes that the same
  object the routes and the future API return.
* `TestClient` turns route tests into ordinary function calls, which is most
  of `tests/test_web.py`.
* Server-rendered HTML with one stylesheet and no JavaScript: this is a
  results table on an internal test box, and a build step would be pure cost.
* Legacy EnzymeX runs on Pyramid. That is not a reason to pick Pyramid here,
  and it costs nothing later; the framework only appears in `app/web/`.

## Persistence

Job results are a JSON document in the job's own directory. There is no job
database.

The copied MySQL is read-only by policy, so a writable job store would be a
second database to deploy, back up and migrate, for data whose entire lifetime
is 24 hours. The JSON document holds exactly what a queue-backed version would
persist, so moving to one later means changing who writes the file, not what
is in it.

## Execution model

Searches run synchronously inside the request, bounded by:

* `ENZYMEX_MAX_CONCURRENT_JOBS`: a process-wide semaphore; excess requests
  get 503 rather than queueing until the box runs out of memory;
* `ENZYMEX_BLAST_TIMEOUT_SECONDS` / `ENZYMEX_HMMER_TIMEOUT_SECONDS`: every
  child is killed by process group on expiry;
* `ENZYMEX_MAX_QUERY_SEQUENCES` and `ENZYMEX_MAX_QUERY_LENGTH`.

Measured on the source-policy fixture build described in the README (1,574
references, 47 profiles, 2 search threads), one sequence through all three
methods took 0.85 to 0.93 s in the post-build checks. Synchronous execution is
adequate for this test server; production should run `run_search` inside
EnzymeX's existing scheduled job rather than its web request.
