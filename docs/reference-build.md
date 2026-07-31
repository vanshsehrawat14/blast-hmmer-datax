# Reference build

Everything the search path reads is produced offline by one command. It is the
only code that touches MySQL, and it never runs during a user request.

```bash
make refbuild                       # export + blast + hmmer + manifest
# equivalently
enzymex-refbuild all
```

Individual steps, for iterating:

```bash
enzymex-refbuild inspect            # read-only report on the copy
enzymex-refbuild export             # enzymesdata -> references.fasta + metadata
enzymex-refbuild blast              # makeblastdb
enzymex-refbuild hmmer              # cluster -> mafft -> hmmbuild -> hmmpress
enzymex-refbuild all --skip-profiles  # BLAST + phmmer only, no profile layer
enzymex-refbuild status             # what is currently built
```

## What it writes

```
var/reference/
  references.fasta          validated protein references, deterministic order
  metadata.sqlite3          ref_id -> description / EC / source / provenance
  skipped.tsv               every rejected row, with a reason
  export_stats.json         counts by reason, source, length distribution
  blastdb/references.*      makeblastdb output
  profiles/profiles.hmm     concatenated profile HMMs
  profiles/profiles.hmm.h3* hmmpress index
  skipped_clusters.tsv      every rejected cluster, with the gate that rejected it
  build_manifest.json       build id, checksums, tool versions, all statistics
  work/                     mmseqs and per-family scratch (removed unless --keep-work)
```

None of it is committed. `var/` is gitignored in full.

## Source policy

The default build accepts only rows whose normalized `enzymesdata.source` is
`swissprot` or `pdb`. The resulting deduplicated `references.fasta` is shared
by BLAST and phmmer and is also the input to profile-HMM construction, so the
three search paths cannot silently use different source sets.

Sequence QC runs first, source selection second, and exact-sequence
deduplication third. If both selected sources carry the same sequence, the
Swiss-Prot row is preferred as canonical metadata; fields are not merged.
Every valid selected PDB row remains available either as a canonical
`reference` or as duplicate provenance in `reference_duplicate`.
`export_stats.json` reports selected, excluded, duplicate and canonical counts
by source. `ENZYMEX_REFERENCE_SOURCES` is part of the build id.

The development fixture exercises this policy but does not contain
PDB-derived sequences. Its `pdb` labels are deterministic test labels on
reviewed Swiss-Prot sequences. A fixture build validates the filtering and
provenance code, not real PDB ingestion or coverage.

## Identifiers

References are `EXR<stable export key>`: stable across rebuilds, unique, and a
single whitespace-free token so BLAST and HMMER cannot split or reinterpret
it. Deflines carry only sanitised fields:

```
>EXR417 EC=5.3.1.9 src=swissprot
```

Free text stays in SQLite. `makeblastdb -parse_seqids` is deliberately *not*
used: it rewrites UniProt-style deflines into `sp|ACC|` form, which then has
to be stripped out of every result row, and nothing here needs `blastdbcmd`
retrieval by identifier.

Profiles are `EXF00001`, `EXF00002`, … numbered by descending cluster size.

## Determinism

Rows are read in primary-key order with a server-side cursor, so memory stays
flat and the FASTA is byte-identical between runs against an unchanged copy.
`tests/test_export.py::test_export_is_byte_identical_on_a_second_run` asserts
this.

The `reference_build_id` is the first 12 hex characters of a SHA-256 over the
FASTA checksum plus the settings that change what is searchable (database and
table name, length and ambiguity filters, export limit, clustering thresholds,
profile gates). So:

* rebuilding unchanged data reproduces the same id;
* changing the data or a filter changes it;
* changing a request-time timeout does not.

Every result row and every results page carries that id.

BLAST index files are excluded from the manifest's checksum list because
`makeblastdb` embeds a creation timestamp, so including them would make an
identical build look non-reproducible.

## Rebuilding after the copy changes

Re-run `make refbuild`. It is fully idempotent: the metadata database is
recreated, the profile database is re-pressed, and stale `.h3*` index files
are removed first. The new build id appears in `/health` and on every
subsequent results page. Old jobs keep the id they were run under, so a stored
result is never silently re-attributed.

## Scaling

Measured on the source-policy development build (2,677 rows → 1,574
Swiss-Prot/PDB-labelled references, WSL2 / Ubuntu 24.04, 4 build threads):

| step | time |
|---|---|
| export (two snapshot scans of 2,677 rows) | 1.0 s |
| makeblastdb | 0.5 s |
| profile layer, including clustering and 47 families | 133.9 s |
| **total** | **137.1 s** |

Where the time goes as the copy grows:

* **export** is linear in rows and dominated by the network round trip. Tens
  of millions of rows are fine; it streams.
* **makeblastdb** is linear and fast.
* **mmseqs clustering** is the step designed for this scale and is the least
  of the worries.
* **MAFFT + hmmbuild is the bottleneck**: one subprocess pair per accepted
  cluster, roughly 3 s each here. A copy producing 10,000 accepted clusters
  would take several hours single-threaded. `ENZYMEX_PROFILE_MAX_MEMBERS` bounds
  the per-cluster cost but not the cluster count. If that becomes a problem,
  raise `ENZYMEX_PROFILE_MIN_MEMBERS` (fewer, larger families) or parallelise
  the per-family loop, which is parallelisable and currently serial for
  simplicity.
* **phmmer at search time** scales with the number of references, not with the
  build. Expect roughly linear growth; it is the first method that will need
  `ENZYMEX_HMMER_TIMEOUT_SECONDS` raised on a large copy.

`ENZYMEX_EXPORT_LIMIT` caps the export for a first trial build on a large
copy. It is part of the build id, so a capped build cannot be mistaken for a
full one.

## Reading the manifest

```bash
python -m json.tool var/reference/build_manifest.json | head -40
```

It records the build id, timestamp, platform, the source database identity
(host, name, table, never credentials), the settings that fed the id,
versions of every tool used, SHA-256 of each artifact, and the full export,
BLAST and HMMER statistics including every skip reason and count.
