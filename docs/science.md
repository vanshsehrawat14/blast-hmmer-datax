# Scientific design

What each method measures, why the HMMER layer is built the way it is, and
which numbers may honestly be compared with which.

## The three searches

| | direction | database for E-value | coverage of the references |
|---|---|---|---|
| `blastp` | query → reference sequences | reference set | 100% |
| `phmmer` | query (as a 1-sequence profile) → reference sequences | reference set | 100% |
| `hmmscan` | query → profile HMMs | profile set | only QC-passing clusters |

`blastp` and `phmmer` are the universal baseline. Both compare the submitted
sequence against every exported reference, so absence of a hit means the
reference set has no detectable relative — not that the profile layer happened
to skip something.

`phmmer` is not redundant with BLAST. It builds a profile from the query and
scores under HMMER's probabilistic model with its own E-value theory. A single
sequence carries no position-specific conservation, so rankings usually track
blastp closely; the value is that the two are independent implementations of
different statistics over identical data, which is exactly what you want when
the question is "how confident should I be in this hit".

## The HMMER decision

**phmmer end to end, plus profile HMMs for a QC-passing subset. Profiles are
built from sequence-similarity clusters, not from EC groups.**

### Why not one profile per EC number

An EC number names a *reaction*, not a family. Enzymes catalysing the same
reaction are frequently non-homologous — "analogous enzymes" are common enough
to have been surveyed systematically (Galperin, Walker & Koonin, *Genome
Research* 8:779, 1998). Aligning every sequence sharing an EC number and
running `hmmbuild` over the result produces a profile whose match states model
an alignment artefact. It will still emit E-values, and they will be
meaningless, which is worse than emitting nothing.

This is not hypothetical. On the reference build used to develop this server,
clustering the export by similarity and then reading off the EC annotation
gives:

| EC | reaction | distinct profiles |
|---|---|---|
| 2.5.1.18 | glutathione transferase | 14 |
| 4.2.1.1 | carbonic anhydrase | 10 |
| 1.15.1.1 | superoxide dismutase | 6 |
| 1.1.1.1 | alcohol dehydrogenase | 5 |
| 5.3.1.9 | glucose-6-phosphate isomerase | 5 |

Superoxide dismutase and carbonic anhydrase are the textbook cases — Cu/Zn,
Mn/Fe and Ni SODs, and the α/β/γ carbonic anhydrase classes, are unrelated
folds doing the same chemistry. A per-EC profile would have merged them.

### The pipeline that is used instead

```
references.fasta
      │
      ▼  mmseqs easy-cluster --min-seq-id 0.35 -c 0.80 --cov-mode 0 --cluster-mode 0
homologous clusters
      │
      ▼  size / fragment / subsample gates            (references/cluster.py)
candidate families
      │
      ▼  mafft --auto --anysymbol
alignment
      │
      ▼  core-column gate                             (references/hmmer_build.py)
      ▼  hmmbuild --amino
profile
      │
      ▼  match-state gate
      ▼  concatenate + hmmpress
profiles.hmm  ──►  hmmscan
```

EC annotation is attached to a family *after* clustering. One EC may therefore
produce several profiles, each reporting `ec_purity` — the share of its
annotated members carrying the consensus EC. A low purity is shown on the
results page as a warning that the EC should not be read as a prediction.

### Clustering parameters, and why

* `--min-seq-id 0.35` — above the alignment twilight zone. Below roughly
  20–35% identity, pairwise alignments stop being reliably correct (Rost,
  *Protein Engineering* 12:85, 1999), and an MSA built from them is not worth
  modelling.
* `-c 0.80 --cov-mode 0` — 80% coverage required on *both* sequences. This is
  the gate that keeps a short fragment from joining a family by matching one
  domain of a large multidomain protein, and it is what makes a single MSA of
  the cluster defensible.
* `--cluster-mode 0` (greedy set cover) — every member meets the threshold
  against its representative, so cluster membership has a stated meaning
  rather than being the result of transitive chaining.
* MMseqs2 is used rather than CD-HIT because it handles the scale a copied
  TrEMBL-derived table can reach (Steinegger & Söding, *Nature Biotechnology*
  35:1026, 2017).

### Quality-control gates

| gate | default | rationale |
|---|---|---|
| minimum members | 5 | below this there is too little data to estimate position-specific emissions; phmmer already covers these sequences |
| length filter | 0.7×–1.4× cluster median | keeps fragments and fusions out of the alignment |
| members after filter | 5 | a cluster split between fragments and full-length loses both sides and is dropped |
| maximum members | 500 (subsampled) | a runtime bound on MAFFT, not a scientific claim — hmmbuild's Henikoff position-based weighting already discounts redundancy |
| alignment core columns | ≥ 0.5 × median member length | rejects gap-dominated alignments, i.e. clusters that are not alignable end to end whatever the clustering thresholds said |
| profile match states | ≥ 40 and ≥ 0.5 × median member length | a profile far shorter than its members is modelling a fragment |

Every rejected cluster is written to `skipped_clusters.tsv` with the gate that
rejected it, and the counts go into the build manifest.

### hmmscan, not hmmsearch

HMMER's documentation notes that `hmmsearch` is faster than `hmmscan` for the
same comparison. `hmmscan` is used anyway, because the E-value depends on the
size of whatever is playing the role of the database:

* `hmmsearch profiles.hmm query.fasta` scores against the *submitted* set. The
  same protein would get a different E-value depending on how many other
  sequences the user happened to paste alongside it.
* `hmmscan profiles.hmm query.fasta` scores against the pressed profile
  database, which is fixed by the build.

Reproducibility of the reported statistic is worth more than the speed
difference at this scale — measured at 0.27 s for one sequence against 64
profiles.

## Reading the numbers

**E-values are not comparable across the three tables.** They are computed
against three different database sizes under two different statistical models.
The results page keeps them in separate tables and says so; do not put them
side by side. Bit scores are normalised for database size and are the more
stable quantity to compare *within* a method.

**Percent identity is BLAST only.** HMMER's tabular output does not report it,
and deriving something identity-shaped from a HMMER alignment would put a
different quantity under the same column name. The field stays null.

**Identity without coverage means nothing.** 50% identity over 16 residues is
noise. The results page greys out query coverage below 50% for this reason.

**BLAST aggregation is deliberately mixed.** For each (query, subject) pair,
E-value, bit score, identity and alignment length come from the single
best-scoring HSP — what BLAST itself reports as the hit's score — while
coverage is summed over all HSPs, because a hit that aligns in three pieces
really does cover three pieces. Subject coverage is computed here by merging
HSP intervals; BLAST+ has no `scovs` field.

**`-max_target_seqs` is not a "top N" filter.** It is a search-time cutoff
that interacts with the traceback stage, and using it as a top-N control gives
results that depend on database order (Shah et al., *Bioinformatics* 35:1786,
2018). It is set generously (500) and the displayed list is truncated by our
own ranking afterwards.

**Composition-based statistics** (`-comp_based_stats 2`, blastp's default) is
passed explicitly so that a future change to it is a visible decision rather
than silent default drift. It is what makes E-values from compositionally
biased queries trustworthy (Yu, Wootton & Altschul, *NAR* 31:3980, 2003).

**Nucleotide input cannot be caught by alphabet.** A, C, G, T, U and N are all
valid amino acid letters, so a pasted gene sequence passes an alphabet check
and then produces nonsense alignments. It is rejected compositionally instead:
≥90% of those six letters over ≥50 residues.

## What this does not establish

Sequence similarity is evidence of homology. Homologues frequently share
function, but not always, and a shared EC number is not established by
similarity alone. This server runs no EC prediction model. In EnzymeX these
tables would sit alongside ECPICK, HIT-EC and CLEAN predictions as supporting
evidence — not replace them.
