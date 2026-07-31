# Configuration

All settings come from the environment or a local `.env`, with the prefix
`ENZYMEX_`. `app/config.py` is the only place they are defined; there are no
constants scattered through the code. `.env.example` is the copy-me template.

Anything listed as feeding the build id changes `reference_build_id` when
altered, so a result can never be attributed to the wrong reference set.

## Copied database

| variable | default | notes |
|---|---|---|
| `ENZYMEX_DB_HOST` | `127.0.0.1` | a **copy**, never production |
| `ENZYMEX_DB_PORT` | `3306` | |
| `ENZYMEX_DB_NAME` | `enzymex_copy` | feeds the build id |
| `ENZYMEX_DB_USER` | `enzymex_ro` | needs `SELECT` only |
| `ENZYMEX_DB_PASSWORD` | — | `SecretStr`; never logged or served |
| `ENZYMEX_DB_TABLE` | `enzymesdata` | feeds the build id |
| `ENZYMEX_DB_CONNECT_TIMEOUT` | `10` | seconds |
| `ENZYMEX_DB_CONFIRM_COPY` | `false` | **required `true`** before any connection |

## Filesystem

| variable | default | notes |
|---|---|---|
| `ENZYMEX_REFERENCE_DIR` | `var/reference` | build artifacts; gitignored |
| `ENZYMEX_JOB_DIR` | `var/jobs` | per-job scratch; gitignored |

Relative paths resolve against the repository root.

## Executables

`ENZYMEX_BLASTP_BIN`, `ENZYMEX_MAKEBLASTDB_BIN`, `ENZYMEX_PHMMER_BIN`,
`ENZYMEX_HMMSCAN_BIN`, `ENZYMEX_HMMBUILD_BIN`, `ENZYMEX_HMMPRESS_BIN`,
`ENZYMEX_MAFFT_BIN`, `ENZYMEX_MMSEQS_BIN`.

Bare names by default, resolved through `PATH`. Set absolute paths when the
service account's `PATH` does not include the conda environment.

## Export filters (all feed the build id)

| variable | default | notes |
|---|---|---|
| `ENZYMEX_REFERENCE_SOURCES` | `swissprot,pdb` | only these normalized `enzymesdata.source` values enter BLAST, phmmer and profile construction; accepts either source alone for comparison builds |
| `ENZYMEX_MIN_SEQUENCE_LENGTH` | `30` | below any real single-domain protein; shorter references give alignments too short for a meaningful E-value |
| `ENZYMEX_MAX_REFERENCE_LENGTH` | `10000` | |
| `ENZYMEX_MAX_AMBIGUOUS_FRACTION` | `0.10` | X/B/Z/J/U/O carry no residue identity |
| `ENZYMEX_EXPORT_LIMIT` | `0` | `0` = whole table; set a cap for a trial build |

## Submission limits

| variable | default |
|---|---|
| `ENZYMEX_MAX_UPLOAD_BYTES` | `1000000` |
| `ENZYMEX_MAX_QUERY_SEQUENCES` | `10` |
| `ENZYMEX_MAX_QUERY_LENGTH` | `5000` |

## Search

| variable | default | notes |
|---|---|---|
| `ENZYMEX_BLAST_EVALUE` | `0.001` | reporting threshold |
| `ENZYMEX_PHMMER_EVALUE` | `0.001` | |
| `ENZYMEX_HMMSCAN_EVALUE` | `0.001` | |
| `ENZYMEX_MAX_HITS_PER_QUERY` | `25` | applied by our own ranking, after the search |
| `ENZYMEX_SEARCH_THREADS` | `2` | per tool invocation |
| `ENZYMEX_BLAST_TIMEOUT_SECONDS` | `120` | |
| `ENZYMEX_HMMER_TIMEOUT_SECONDS` | `300` | phmmer is the one that will need raising on a large copy |
| `ENZYMEX_MAX_CONCURRENT_JOBS` | `2` | excess submissions get 503 |
| `ENZYMEX_ENABLE_BLAST` | `true` | a disabled method is shown as disabled, not hidden |
| `ENZYMEX_ENABLE_PHMMER` | `true` | |
| `ENZYMEX_ENABLE_PROFILE_HMM` | `true` | |

`blast_max_target_seqs` is fixed at 500 in `config.py` rather than exposed: it
is a search-time cutoff, not a display limit, and `MAX_HITS_PER_QUERY` is the
knob that actually controls what is shown. See `docs/science.md`.

## Reference build

| variable | default | notes |
|---|---|---|
| `ENZYMEX_BUILD_THREADS` | `4` | MMseqs2, MAFFT, hmmbuild |
| `ENZYMEX_CLUSTER_MIN_SEQ_ID` | `0.35` | build id; above the twilight zone |
| `ENZYMEX_CLUSTER_COVERAGE` | `0.80` | build id; bidirectional |
| `ENZYMEX_PROFILE_MIN_MEMBERS` | `5` | build id; raise it if the MAFFT loop is too slow |
| `ENZYMEX_PROFILE_MAX_MEMBERS` | `500` | build id; runtime bound, not a scientific choice |
| `ENZYMEX_PROFILE_MIN_MATCH_STATES` | `40` | build id |
| `ENZYMEX_BUILD_TIMEOUT_SECONDS` | `7200` | per external tool call |

## Web

| variable | default | notes |
|---|---|---|
| `ENZYMEX_JOB_RETENTION_HOURS` | `24` | swept on each new submission |
| `ENZYMEX_KEEP_RAW_OUTPUTS` | `true` | `false` keeps only the normalized result |
| `ENZYMEX_LOG_LEVEL` | `INFO` | |

## Checking what took effect

```bash
make status                      # build id, artifacts, method availability
curl -s localhost:8000/health    # the same, plus tool versions and the copy
```
