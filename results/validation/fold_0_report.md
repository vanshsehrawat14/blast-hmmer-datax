# External BLAST/HMMER validation

- Fold: `0`
- BLAST baseline input: `test_fold_0.tsv`

## Split and shared baseline

- Train: 160,637 sequences, 65,548,858 residues
- Validation: 19,523 sequences
- Test: 19,567 sequences
- Train/test accession overlap: 0
- Train/test exact-sequence overlap: 0
- Shared TSV queries with hits: 19,519; no hits: 48
- Raw top-hit identity: median 86.7%; 44.12% are at least 90% identical and 16.80% are at least 99% identical

| Shared-output selection | Exact EC set | Any EC overlap | EC micro-F1 |
|---|---:|---:|---:|
| BLAST first subject | 98.18% | 98.88% | 0.9856 |
| Application E-value/bit-score rank | 98.17% | 98.88% | 0.9855 |
| Notebook maximum identity | 97.95% | 98.72% | 0.9837 |

## BLAST 2.16 reproduction

- Reported baseline: `BLAST+ 2.5.0` with `-evalue 1e-05 -comp_based_stats 2 -max_target_seqs 100 -outfmt 6 -num_threads 16`
- Reproduction version: `blastp: 2.16.0+`
- Reproduction cohort: 500 queries
- The cohort includes all 48 shared no-hits; hit-order parity uses the 452 queries with hits in both outputs.
- Search time: 131.44 s
- Raw top-1 agreement with the reported BLAST+ 2.5.0 output: 452/452 (100.00%)
- Raw top-score agreement (E-value and bit score): 447/452 (98.89%); 0 tied-score cases selected a different subject ID
- Identical ordered top 25: 98.67%
- Identical top-25 subject set: 99.56%
- Mean top-25 set Jaccard: 0.9994

## Leakage-safe copied-development EnzymeX benchmark

- Source reference build: `32abd580b689`
- Selected source labels: swissprot, pdb
- Canonical references by source: swissprot=1,387, pdb=187
- References: 1,574 source, 143 records matching test hashes removed, 1,431 searched
- Rebuilt profiles: 44
- Of 1,407 filtered references exactly matching a supplied CSV sequence, 1,338 (95.10%) have the same normalized EC set
- Reference-covered selected slice: 349/19,567 queries (1.78%)
- Exact-set eligible selected slice: 340/19,567 queries (1.74%)
- Profile-common single-label selected slice: 279/19,567 queries (1.43%)
- Exact query/reference sequence overlap: 0

Source labels in this section come from the copied table and are not independent upstream-provenance verification. In the committed development fixture, `pdb` is a synthetic label on reviewed Swiss-Prot sequences, so this report does not validate genuine PDB-derived data.

### Sequence-reference scope

Runtime is for the whole reference-covered batch. Exact-set concordance uses only queries whose full truth set exists in the reference. These are single local invocations, not stable performance estimates.

| Method | Runtime | Any EC overlap (n=349) | Exact EC set (n=340) | Any-EC hit@5 (n=349) | Any-EC hit@25 (n=349) |
|---|---:|---:|---:|---:|---:|
| blastp | 7.81 s | 306/349 (87.68%) | 279/340 (82.06%) | 307/349 (87.97%) | 308/349 (88.25%) |
| phmmer | 130.36 s | 303/349 (86.82%) | 280/340 (82.35%) | 307/349 (87.97%) | 307/349 (87.97%) |

BLAST and phmmer differ by only 3 queries on top-1 overlap and 1 on exact sets. One fold does not establish a winner.

The exact-set slice spans 30 complete EC labels. Its unweighted per-EC top-1 token concordance is 66.07% for BLAST and 65.40% for phmmer; the lower macro rates expose weaker rare-label behavior.

### Common profile scope

The BLAST top-hit median identity in this selected slice is 85.1%.

| Method | Queries with hits | Top-1 EC overlap (n=279) | Any-EC hit@5 (n=279) | Any-EC hit@25 (n=279) | First-overlap MRR |
|---|---:|---:|---:|---:|---:|
| blastp | 279/279 | 279/279 (100.00%) | 279/279 (100.00%) | 279/279 (100.00%) | 1.0000 |
| phmmer | 279/279 | 279/279 (100.00%) | 279/279 (100.00%) | 279/279 (100.00%) | 1.0000 |
| hmmscan | 272/279 | 272/279 (97.49%) | 272/279 (97.49%) | 272/279 (97.49%) | 0.9749 |

The common-profile slice is the easiest part of the selected cohort. Outside it, BLAST produced hits for 35/70 queries and its top hit shared an EC for 27/70.

The Swiss-Prot reproduction tests cross-version hit-order concordance under the reported search parameters. The EnzymeX table measures top-hit EC concordance within the copied development reference's covered labels. They answer different questions and should not be combined into one accuracy.

1,407/1,431 filtered development-reference sequences occur verbatim in the supplied Swiss-Prot dataset, which also supplies every query. Even after exact-sequence removal this is a close-homolog benchmark. The accession split removes exact sequences but does not impose a homology or identity cutoff.

The methods were run only on the 349 queries selected using known truth labels; the other 19,218 fold queries were not searched against EnzymeX. These results therefore do not measure out-of-scope false assignments, specificity, broad EC prediction, or remote-homology performance.

All methods use the current E-value threshold of 1e-3, but their E-values come from different models and database sizes. The table describes current default behavior, not a calibrated method contest.
