# Testing

```bash
make test           # everything available on this machine
make test-unit      # no external tools, no database
make test-tools     # the end-to-end suite (needs BLAST+/HMMER/MAFFT/MMseqs2)
make test-db        # the MySQL layer (needs credentials for a COPY)
```

157 tests, ~20 s on a laptop. Tests that need something absent skip rather
than fail, so a fresh clone with no tools installed still runs the unit suite.

| file | covers |
|---|---|
| `test_fasta.py` | parsing, normalization, every rejection reason, internal id assignment, nucleotide detection |
| `test_parsers.py` | BLAST HSP folding and coverage, the phmmer/hmmscan coordinate role-flip, malformed output, ranking |
| `test_export.py` | export against a stubbed MySQL: null/malformed rows, dedup with provenance, EC and source normalization, missing columns, determinism |
| `test_cluster.py` | every QC gate, alignment core columns, pairwise identity sampling, consensus EC with multi-EC members |
| `test_jobs.py` | id generation, path traversal, directory isolation and permissions, persistence, retention sweep |
| `test_service.py` | method selection, disabled methods, partial failure, metadata resolution, concurrency bound, flat-row schema |
| `test_web.py` | every validation path through HTTP, results rendering for ok/no-hit/failed/disabled, HTML escaping, CSV/JSON downloads, health |
| `test_database.py` | the real driver: copy acknowledgement, identifier validation, read-only session, key-ordered streaming |
| `test_e2e.py` | real tools, real artifacts, real HTTP |
| `test_pipeline.py` | the original proof-of-concept outputs |

## The end-to-end test

`tests/test_e2e.py` builds a genuine reference set — `makeblastdb`, MMseqs2
clustering, MAFFT, `hmmbuild`, `hmmpress` — from the 20 curated EC 1.1.1.1
sequences committed under `data/raw/`, then runs the real tools against the
held-out positive and negative queries from the same set. It asserts:

* positives produce a top hit at E < 1e-20 with EC and source resolved and
  query coverage above 50%, from both BLAST and phmmer;
* the alpha-amylase and hexokinase negative controls produce nothing stronger
  than E = 1e-10 from any method;
* results attach to the correct submitted sequence when several are submitted
  together;
* profile hits carry a family id, member count and query coordinates;
* a missing executable, a nonzero exit, a timeout and absent artifacts each
  produce a structured failure rather than an exception;
* two jobs get separate directories with their own raw outputs;
* the whole thing works through HTTP — form post, 303, results page, CSV and
  JSON downloads.

## Manual verification procedure

Against a real copied database:

```bash
cp .env.example .env && $EDITOR .env       # CONFIRM_COPY=true
make inspect                               # sanity-check the table
make refbuild                              # minutes
make serve
```

Then:

1. `curl -s localhost:8000/health | python -m json.tool` → `"status": "ok"`,
   a `reference_build_id`, non-zero `reference_sequences`.
2. Open `http://127.0.0.1:8000/`, paste a protein you expect to be in the
   references, submit. Expect a BLAST table with EC and source populated, a
   phmmer table, and a profile table if the profile layer built.
3. Submit a non-enzyme (human histone H4 is a good one) in the same job.
   Expect "No hits" for every method, under its own heading.
4. Submit a nucleotide sequence. Expect rejection with an explanation.
5. Submit 11 sequences with the default limit of 10. Expect rejection.
6. Download the CSV. Every row carries the same `reference_build_id` that
   `/health` reports.

## Runtime measured on the development build

2,380 references, 64 profiles, `ENZYMEX_SEARCH_THREADS=2`, WSL2 / Ubuntu
24.04. Medians of three runs.

| queries | method | wall clock |
|---|---|---|
| 1 | blastp | 0.30 s |
| 1 | phmmer | 1.16 s |
| 1 | hmmscan | 0.27 s |
| 1 | all three | 1.96 s |
| 3 | all three | 3.16 s |
| 10 | all three | 9.01 s |

Peak resident memory: 55 MB in the web process, 83 MB across children.

phmmer dominates and scales with the number of references; the other two
scale with query count more than database size at this scale. These are
measurements on this build, not performance claims about the tools.
