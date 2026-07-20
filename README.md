# blast-hmmer-datax

Runs BLAST, HMMER, and DIAMOND against a set of EC 1.1.1.1 (alcohol
dehydrogenase) protein sequences from UniProt, and normalizes their outputs
into one schema so the methods can be compared. Reproducible from a single
`make all`. Part of a UNLV independent study.

## Quick start

```bash
bash scripts/00_setup.sh   # install micromamba + create the environment
make all                   # download data, run the tools, parse, verify
```

`make all` writes the comparison to
[`results/comparison/comparison_report.md`](results/comparison/comparison_report.md).

| Command | Does |
|---|---|
| `make env` | Create the conda environment from `environment.yml` |
| `make data` | Download the sequences from the UniProt REST API |
| `make blast` | Build the BLAST database, run `blastp` |
| `make hmmer` | Run `phmmer`; align references, `hmmbuild`, `hmmsearch` |
| `make diamond` | Optional: run DIAMOND over the same sequences |
| `make parse` | Normalize all results into CSV/JSON + comparison report |
| `make test` | Verify the outputs |
| `make versions` | Print installed tool versions |
| `make clean` | Delete generated results and processed data |

## The data

**EC 1.1.1.1** is alcohol dehydrogenase — an oxidoreductase that converts
alcohols to aldehydes/ketones using NAD+. EC numbers classify enzymes by
reaction, not by sequence, so two proteins sharing an EC number are not
guaranteed to have similar sequences.

`scripts/01_download_uniprot.py` pulls three disjoint sets of reviewed
(Swiss-Prot) sequences, deterministically (sorted by accession, deduplicated
by accession and by sequence):

- **20 reference** EC 1.1.1.1 sequences → the BLAST/DIAMOND database and the
  HMMER profile. Span bacteria, fungi, plants, and animals.
- **5 positive queries** — further EC 1.1.1.1 sequences, held out. Should match.
- **2 negative controls** — alpha-amylase (EC 3.2.1.1) and hexokinase
  (EC 2.7.1.1). Should not match.

Full provenance is in [`data/raw/metadata.tsv`](data/raw/metadata.tsv) and
[`data/raw/provenance.json`](data/raw/provenance.json).

## The methods

**BLAST** (`blastp`) compares a query against each database sequence
individually and returns local alignments. It has no concept of a family.

**DIAMOND** does the same job as `blastp` but is built for speed on large
databases, trading sensitivity for throughput. Run in two modes:
`--very-sensitive` (label `diamond`, closest to blastp) and default (label
`diamond-default`). Optional.

**HMMER** scores sequences against a profile hidden Markov model, which encodes
per-position conservation across a family — so it can recognise a distant
member that no single family sequence resembles closely. A profile has to come
from somewhere, which is why HMMER is run two ways:

| Program | Input | Output |
|---|---|---|
| `phmmer` | one sequence | searches a sequence database with it (like blastp) |
| `hmmbuild` | a multiple alignment | builds a profile `.hmm` |
| `hmmsearch` | a profile `.hmm` | searches a sequence database with it |

A single sequence carries no position-specific information, so `phmmer` behaves
much like BLAST. The profile pipeline adds a MAFFT alignment step to capture
what the family conserves:

```
blastp / phmmer / diamond:  query sequence  ->  20 reference sequences
profile:  20 references -> MAFFT -> hmmbuild -> profile.hmm -> hmmsearch -> queries
```

For `hmmsearch` the profile is the query and the test proteins are the
database — the reverse of the others. The parser flips this back so every
normalized row is query-centric.

## The scores

- **E-value** — expected number of hits this good by chance in a database this
  size. Lower is better. Depends on database size, so E-values are **not
  comparable across methods** here (they search databases of different sizes).
  BLAST prints `0.0` when the value underflows — read it as "< ~1e-180".
- **Bit score** — alignment score normalized to be independent of database
  size. Higher is better; comparable across searches.
- **Sequence identity (%)** — fraction of aligned positions that are identical.
  Meaningless without length/coverage: a 50%-identical 16-residue hit is noise.
  BLAST and DIAMOND report it; HMMER does not.
- **Coverage** — fraction of the sequence that aligned. Computed per method:
  `qcovs` (BLAST), `qcovhsp` (DIAMOND — per-HSP, not identical in meaning), and
  from HMMER's domain-table coordinates for `phmmer`/`hmmsearch`. Comparable in
  spirit, not to the decimal.

## Results

Best hit per query (E-value; lower is a stronger match). Full table in
[`results/comparison/comparison_report.md`](results/comparison/comparison_report.md).

| Query | Set | blastp | phmmer | hmmsearch | diamond | diamond-default |
|---|---|---|---|---|---|---|
| O13309 | positive | 0.0 | 2.1e-198 | 5.7e-101 | 1.5e-213 | 1.5e-213 |
| O19053 | positive | 0.0 | 6.5e-171 | 1.5e-99 | 1.3e-182 | 1.3e-182 |
| O45687 | positive | 2.7e-112 | 4.3e-112 | 1.2e-71 | 2.3e-118 | 2.3e-118 |
| O31186 | positive | 2.1e-89 | 2.1e-91 | 7e-60 | 2.4e-94 | 2.4e-94 |
| O07737 | positive | 8.7e-54 | 1.8e-52 | 1.3e-27 | 1.8e-48 | ***missed*** |
| A0A0K0JFP3 | **negative** | 0.56 | *no hit* | *no hit* | *no hit* | *no hit* |
| A0A096XJN4 | **negative** | 1.9 | *no hit* | *no hit* | *no hit* | *no hit* |

On this 27-sequence run:

- Every method found all five positive queries (default-mode DIAMOND excepted,
  below) and ranked them in the same order.
- BLAST reports the negative controls as short noise hits: 16–43 residues,
  3–9% coverage, bit 18–22, E 0.56–9.3 — 50+ orders of magnitude off the real
  hits. One is 50% identical, which is why identity alone is a bad filter.
- Both HMMER methods and sensitive DIAMOND report nothing for the negatives.
- DIAMOND's default mode misses `O07737` (~32% identity) entirely — the
  documented speed/sensitivity trade-off, not a bug. `--very-sensitive` finds
  it. Note the trap: default's *worst* E-value (2.4e-94) looks better than
  sensitive's (1.8e-48) only because it dropped the hard query.

The reference set is small and accession-sorted, not a representative sample.
No accuracy claims follow from it.

## Normalized output

`scripts/04_parse_results.py` maps every method to one schema in
`results/comparison/all_hits.{csv,json}`:

`method`, `query_accession`, `query_set`, `query_organism`, `query_ec`, `hit`,
`hit_organism`, `hit_ec`, `rank`, `evalue`, `bitscore`, `percent_identity`
(BLAST/DIAMOND only), `coverage`.

Two things the parser normalizes: it flips the `hmmsearch` query/target roles
so `query_accession` always means the test protein, and it strips the
`sp|ACCESSION|` wrapper that `makeblastdb -parse_seqids` adds so accessions
join against the metadata.

## Layout

```
scripts/       00_setup, 01_download, 02_blast, 03_hmmer, 04_parse, 05_diamond
data/raw/      downloaded FASTA + metadata.tsv + provenance.json (committed)
data/processed/  alignment, profile HMM, search indexes (gitignored)
results/       blast/ hmmer/ diamond/ comparison/  (tabular + normalized output)
tests/         test_pipeline.py
docs/          setup_notes.md
```

`results/` and `data/raw/` hold the committed output of this run. Regenerable
dumps (logs, full-text alignments, database indexes) are gitignored; everything
is rebuilt by `make all`.

## Reproducibility

Downloads are scripted and deterministic; `provenance.json` records the exact
queries, the selection rule, and every accession. UniProt changes between
releases, so the committed FASTA/metadata are the record of what this run used
(`retrieval_date` in `metadata.tsv` pins when).

Developed on Windows 11 / WSL2 (Ubuntu 24.04.3). The environment uses
micromamba, not `apt` — it needs no root and pins exact versions via
`environment.yml`. Details and limitations in
[`docs/setup_notes.md`](docs/setup_notes.md).

## Attribution

Tool versions, exact commands, citations, and licenses:
[`ATTRIBUTION.md`](ATTRIBUTION.md).
