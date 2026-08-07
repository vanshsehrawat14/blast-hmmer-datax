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

## The Agave cross-check, 2026-08-04 to 2026-08-07

The EnzymeX team asked for a cross-check of an existing BLAST annotation of the
Agave proteome. The inputs were the notebook, the raw BLAST output, the query
set, the Swiss-Prot EC table and the shipped predictions. Nothing here runs in
this project's code path; it is recorded because the same best-hit question
decides what this application shows a user.

The search itself was `blastp` (BLAST+ 2.5.0) with `-evalue 1e-5
-comp_based_stats 2 -max_target_seqs 100 -outfmt 6`. Those parameters are
sound, and `-comp_based_stats 2` is blastp's own default rather than an added
choice. The shipped file reproduces from the notebook exactly: all 42,295 rows
identical, so the file matches the code that made it.

The finding is the selection rule. The notebook takes the best hit with

```python
df_hit.loc[df_hit.groupby("qseqid")["pident"].idxmax()]
```

`-outfmt 6` emits one row per HSP, not one per hit, and `pident` is computed
over the aligned region alone. Maximising it therefore selects the highest
local identity, which is often a short fragment, and 75,873 query-subject
pairs in this run have more than one HSP. Over the 15,757 annotated queries,
4,751 winning alignments (30.2%) covered less than half the query. The clearest
case is `AgateWBH1.16G051300.1.p`, 1,834 aa: EC 2.1.1.369 was taken from a
46-residue alignment at bit score 67, while a 1,023-residue alignment at bit
score 1001 carrying EC 1.14.11.67 was discarded. A further 44 queries had a tie
on maximum `pident` across hits with different ECs, so their annotation was
decided by row order in the TSV.

The annotation's author confirmed on 2026-08-07 that every hit had already
passed the `1e-5` threshold and agreed that ranking by E-value is the better
rule. The
re-annotation therefore keeps the same search, the same threshold and the same
schema, and changes only the key: lowest E-value, then highest bit score, then
subject accession so the outcome no longer depends on row order.

| | max `pident` | min E-value |
| --- | --- | --- |
| queries annotated | 15,757 | 15,757 |
| best-hit subject differs | | 7,483 (47.5%) |
| assigned EC differs | | 2,133 (13.5%) |
| median bit score, where the subject changed | 134 | 233 |
| median aligned length, where the subject changed | 204 | 335 |
| median query coverage | 0.827 | 0.923 |
| coverage below 50% | 4,751 (30.2%) | 2,406 (15.3%) |
| coverage below 20% | 1,829 (11.6%) | 496 (3.1%) |

No query gained or lost an annotation; the threshold did not move. Of the 7,483
queries whose subject changed, 24 end on a lower bit score than before. That is
composition-based statistics rescaling per subject, which lets the reported
E-value and bit-score orders disagree very slightly; bit score is the more
consistent key, but at 24 of 15,757 the difference does not justify departing
from what was agreed.

Two things this does not fix. E-value ranking reduces the short-alignment
problem but does not remove it: 2,406 annotations still rest on an alignment
covering under half the query, and a coverage floor is a separate decision
nobody has made. And a best hit of any kind transfers the EC of one protein,
so a multi-domain query still inherits the annotation of whichever domain
aligned best.

The same reasoning is why this application ranks by E-value with bit score as
the tiebreak, reports query coverage on every hit, and returns a ranked list
rather than a single answer.
