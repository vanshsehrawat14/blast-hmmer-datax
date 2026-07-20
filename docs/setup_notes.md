# Setup notes

Environment decisions, constraints, and assumptions made while building this
proof of concept.

## Operating system

| | |
|---|---|
| Host | Windows 11 Home, build 10.0.26200 |
| Execution environment | WSL2, Ubuntu 24.04.3 LTS |
| Environment manager | micromamba 2.8.1 |

BLAST+, HMMER and MAFFT are Unix tools. NCBI does ship a Windows BLAST+
installer, but HMMER and MAFFT have no supported native Windows builds, so a
single Linux environment for all three is simpler and keeps one set of commands
in the documentation. WSL2 was already available on this machine.

## Why micromamba instead of apt

The obvious approach on Ubuntu is:

```bash
sudo apt install ncbi-blast+ hmmer mafft
```

This was not used. Two reasons:

1. **`sudo` requires a password on this WSL installation**, which cannot be
   supplied non-interactively from an automated setup script.
2. **`apt` pins to whatever the distribution ships**, which differs between
   Ubuntu releases. `environment.yml` pins exact versions, which is what
   "reproducible on another computer" actually requires.

micromamba installs entirely under `$HOME`, needs no root, and resolves the
same pinned versions on any Linux host. It is a single ~10 MB static binary
with no bootstrap dependency, which is why it is preferred here over a full
Anaconda or Miniconda install.

The `Makefile` accepts `micromamba`, `mamba` or `conda`, whichever it finds
first, so an existing conda installation works without changes.

## Assumptions

- **UniProt entries with EC 1.1.1.1 are a reasonable proxy for "the same
  enzyme family."** This is not strictly true — EC numbers classify reaction
  chemistry, not sequence ancestry — and several reference entries carry
  additional EC numbers (for example `1.1.1.284`, S-(hydroxymethyl)glutathione
  dehydrogenase). Entries were kept as long as 1.1.1.1 is among their
  annotations.
- **Accession-sorted selection is acceptable for a proof of concept.** It is
  deterministic and traceable, which is what was required, but it is not a
  random or phylogenetically balanced sample. The resulting set happens to
  include three *Cochliobolus* and three *E. coli* entries.
- **Permissive score thresholds are intentional.** Both BLAST and HMMER were
  left at default reporting thresholds so that negative-control behaviour is
  visible in the output rather than filtered away.
- **`fragment:false` is trusted to exclude partial sequences.** No additional
  length-based filtering was applied.

## Things that surprised us

- `makeblastdb -parse_seqids` recognises UniProt accession patterns and
  rewrites the FASTA ID `A1L4Y2` as `sp|A1L4Y2|` in all BLAST output. The
  parser strips this so accessions join against `metadata.tsv`.
- `hmmsearch` reverses the query/target roles relative to `blastp` and
  `phmmer`: the profile is the query and the sequences being tested are the
  database. The parser swaps them back so the normalized table is consistently
  query-centric.
- The initial UniProt query returned 250 entries, of which 222 survived
  deduplication and EC filtering — a reminder that Swiss-Prot contains many
  near-identical entries for the same protein across closely related strains.
- DIAMOND's **default mode silently missed** the most distant positive query
  (`O07737`, ~32% identity), which every other method found. It reports no
  error — the hit simply is not there. `--very-sensitive` recovers it. Anything
  built on DIAMOND should set sensitivity deliberately rather than accept the
  default.
- DIAMOND does not implement BLAST's `qcovs` output field. `diamond blastp`
  fails with `Invalid output field: qcovs`; the nearest equivalent is
  `qcovhsp`, which is per-HSP rather than summed across HSPs.

## Limitations

- **27 sequences is a demonstration, not an evaluation.** No accuracy,
  sensitivity or specificity claim can be supported at this scale.
- **E-values are not comparable across methods.** They depend on database size,
  and the searches use different databases (20 sequences for `blastp`,
  `phmmer` and DIAMOND; 7 for `hmmsearch`).
- **No speed comparison was made.** DIAMOND exists to be fast on large
  databases; at 20 sequences every tool finishes instantly, so this repository
  says nothing about relative performance and no timings were recorded.
- **Coverage means three different things** across the methods (`qcovs` for
  BLAST, `qcovhsp` for DIAMOND, domain-table coordinates for HMMER). The
  column is comparable in spirit, not to the decimal.
- **Only two negative controls.** Enough to show the tools distinguish
  unrelated proteins; not enough to characterise a false-positive rate.
- **UniProt is a moving target.** Re-running `make data` against a future
  release may return a different set. The committed FASTA and metadata files
  are the record of what this run used.
- **MAFFT `--auto` chooses its strategy from input size**, selecting L-INS-i
  here. A different reference set could select a different algorithm, so the
  alignment step is reproducible for this input but not strategy-stable in
  general.

## Verifying an install

```bash
make versions   # print resolved tool versions
make all        # full pipeline, ends with the test suite
make test       # checks only
```

`tests/test_pipeline.py` verifies sequence counts, that no negative control is
annotated EC 1.1.1.1, that every expected output file exists and is non-empty,
that each sensitive method found all five positive queries, and that positives
beat negatives by at least ten orders of magnitude in E-value.

DIAMOND checks are skipped automatically when `make diamond` has not been run,
so the test suite passes on a BLAST-and-HMMER-only install. When DIAMOND is
present, the suite also asserts that its default mode finds no more queries
than `--very-sensitive` — so a future DIAMOND release that changes default
sensitivity shows up as a test result rather than a silent change in the
report.
