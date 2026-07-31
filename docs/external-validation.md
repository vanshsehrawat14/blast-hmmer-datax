# External validation

This benchmark answers two separate questions:

1. Does the BLAST command used by this project reproduce the supplied
   Swiss-Prot fold under the same search parameters?
2. How do BLAST, phmmer and the profile-HMM layer behave within the EC scope
   of the copied EnzymeX reference set?

Combining those into one accuracy number would confuse implementation parity
with database coverage. The supplied fold uses 160,637 training sequences;
the source-policy development build has 1,574 Swiss-Prot/PDB-labelled
references and covers far fewer ECs.

## Inputs

The benchmark expects these external files:

- `ec_df_2026_0723.csv`
- `fold_setting_2026_0723.pkl`
- `test_fold_0.tsv`

They are not committed. The CSV, pickle and raw TSV are about 260 MB together,
and the pickle must be treated as trusted input. Its loader permits only the
NumPy array types used by the supplied file and rejects other globals.

Run:

```bash
make validate-external \
  VALIDATION_ANNOTATIONS=/path/to/ec_df_2026_0723.csv \
  VALIDATION_FOLDS=/path/to/fold_setting_2026_0723.pkl \
  VALIDATION_BASELINE=/path/to/test_fold_0.tsv
```

The default BLAST 2.16 reproduction uses 500 deterministic fold queries,
including every query that had no hit in the shared output. A full 19,567
query reproduction takes several hours on the development laptop. Pass
`--reproduction-queries 0` directly to `scripts/validate_external.py` when a
full run is worth that cost.

## Leakage controls

Before any search, the command checks that train, validation and test
accessions are disjoint and that their normalized protein-sequence hashes are
disjoint. It also checks that every shared BLAST query belongs to the test
partition and every subject belongs to training.

For the EnzymeX comparison, every reference whose SHA-256 matches any fold
test sequence is removed before the temporary BLAST database, phmmer FASTA,
clusters or profile HMMs are built. Filtering only `qseqid == sseqid` is not
enough: EnzymeX subjects use `EXR...` identifiers, and the same sequence can
appear under another accession.

Generated FASTA, indexes, raw output and hit diffs stay under
`var/validation/fold_0/`, which is gitignored. A rerun builds in a staging
directory and replaces the prior raw evidence only after every method succeeds.
The small report is written to
`results/validation/`.

## Cohorts and metrics

The Swiss-Prot report keeps all 19,567 queries in the denominator, including
successful no-hit cases. It reports the raw BLAST first subject, the
application's E-value/bit-score ranking, and the notebook's maximum-identity
selection. Reproduction parity is measured by raw top-1 agreement, ordered
top-25 agreement and top-25 set overlap.

The EnzymeX report uses three nested cohorts:

- **reference covered**: the truth shares at least one complete EC with the
  filtered sequence reference;
- **exact-set eligible**: every truth EC is represented, so exact-set
  concordance has a reachable ceiling;
- **profile-common single-label**: one truth EC which also occurs as a profile
  consensus, used for the clean all-method comparison.

Primary biological output is top-hit EC concordance, not general EC prediction
accuracy. Any-EC hit rate at 5 and 25, reciprocal rank of the first
EC-overlapping hit, and per-EC support are also recorded. E-values are never
compared between methods or databases.

The data comes from the same Swiss-Prot release as nearly all development
references and mostly contains close homologs. It can validate the runner,
leakage handling and conditional retrieval behavior. It cannot establish
remote-homology performance, broad functional coverage, or production
EnzymeX integration.

The development fixture's `pdb` values are synthetic source labels on reviewed
Swiss-Prot sequences. This benchmark therefore exercises the combined-source
selection, deduplication and search paths, but it does not validate genuine
PDB-derived sequences or PDB provenance.
