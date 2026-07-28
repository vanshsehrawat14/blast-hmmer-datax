# What the pipeline does

Nine questions about how a submitted sequence becomes a row on the results
page, answered against build `261967e8d173` (2,380 references, 64 profiles).

```
OFFLINE BUILD                 minutes, run by hand or on a schedule

  enzymesdata  ->  export  ->  references.fasta  ->  makeblastdb
  (MySQL copy)     validate     2,380 refs          cluster -> MAFFT -> hmmbuild
   2,677 rows      dedupe                           metadata.sqlite3

============ a user request never crosses this line ============

ONLINE SEARCH                 seconds, one per submission

  FASTA  ->  blastp / phmmer   ->  metadata lookup  ->  results page
  Q1, Q2     hmmscan               local SQLite, ro     three tables
```

The web process holds no MySQL connection and imports no MySQL driver.

## Where the copied database enters

One place, once per build: `app/references/export.py` calling
`app/references/db.py` during `enzymex-refbuild export`. That is the only code
path in the repository that opens a MySQL connection.

Three guards sit on it. The build refuses to connect unless
`ENZYMEX_DB_CONFIRM_COPY=true` is set, so pointing the tool at production takes
two deliberate edits rather than one careless hostname change. Every session
issues `SET SESSION TRANSACTION READ ONLY`, so even an over-privileged account
cannot write. The password lives in a `SecretStr`, so it is absent from logs,
from `repr(settings)` and from `/health`, which reports only
`user@host:port/database`.

Everything a search needs afterwards is in `metadata.sqlite3`, opened
`mode=ro`. That indirection buys two things beyond safety: metadata and search
indexes are versioned together under one build id, so a hit can never resolve
against annotation from a different build, and the request path has no network
hop in it.

## How the reference FASTA is created

Rows stream out of `enzymesdata` ordered by primary key, which is what makes
the output byte-stable between runs and the build id meaningful. Each sequence
is normalised (case folded, whitespace and residue numbering stripped, gap
characters removed, a trailing `*` dropped) and then tested: internal stop
codon, non-IUPAC letters, length outside 30 to 10,000, more than 10% ambiguous
residues, or a composition that looks like nucleotides pasted into a protein
column.

Survivors get the identifier `EXR<primary key>`, restricted to alphanumerics
because BLAST and HMMER split a defline at whitespace and BLAST reinterprets
`|`. Identical sequences are merged by SHA-256: keeping them would make BLAST
return the same alignment several times and inflate the database size the
E-value is computed against. The merged rows are not discarded, they go to a
`reference_duplicate` table so a hit still traces back to every database row it
represents.

```
>EXR2306 EC=1.15.1.1 src=swissprot
MLSRAVCGTSRQLAPVLGYLGSRQKHSLPDLPYDYGALEPHINAQIMQLHHSKHHAAYVNN
LNVTEEKYQEALAKGDVTAQIALQPALKFNGGGHINHSIFWTNLSPNGGGEPKGELLEAIK
```

Every rejection is counted by reason and listed in `skipped.tsv`. On this
build: 2,677 rows in, 2,380 references out, 297 skipped (244 duplicate
sequences, 47 too short, one each of null, empty, internal stop and nucleotide
text).

## Why BLAST databases are built offline

Partly because they have to be. `blastp` cannot search a flat FASTA file, it
needs the index files `makeblastdb` writes. But the real reason is cost.
Rebuilding inside a request would mean re-reading the whole table, forking
MAFFT once per family, and letting one submission stall every other. Here the
BLAST index takes 0.9 s while the full build takes 201 s, roughly 97% of it
MAFFT plus `hmmbuild` across 64 families. On a real copy that grows with the
table.

The versioning argument matters as much. Because the FASTA, the BLAST index,
the profile database and the metadata store are written by one command under
one build id, a hit and its EC annotation are guaranteed to come from the same
snapshot. Build the index lazily and that guarantee disappears the first time
the copy changes mid-flight.

## What blastp does

Local pairwise alignment. It seeds on short matching words, extends them into
high-scoring pairs, and reports each surviving alignment with an E-value, a bit
score, a percent identity and an alignment length. Composition-based statistics
are on (`-comp_based_stats 2`), which keeps E-values honest for a
compositionally biased query.

It has no concept of a protein family. Every hit is one query against one
reference.

Two conventions are applied on top of its output. BLAST emits one row per
high-scoring pair, so the parser folds those into one row per reference:
identity, alignment length, E-value and bit score describe the single
best-scoring pair, while coverage is summed across all of them, because a hit
that aligns in three pieces really does cover three pieces. And the top-25 list
is ranked here rather than taken from `-max_target_seqs`, which is a
search-time cutoff and not a best-N filter (Shah et al., *Bioinformatics* 2018).

## What phmmer does

It turns the submitted sequence into a profile and scores every reference
against it under HMMER's probabilistic model. No build step is involved: it
reads `references.fasta` directly, which is why it covers 100% of the export
and is the baseline the profile layer is measured against.

A single sequence carries no position-specific conservation, so in practice
phmmer ranks much like BLAST. The value of running both is that the scoring
model, the statistics and the implementation are independent. On the demo query
the two agree on the same top four references in the same order, which is a
useful signal precisely because nothing forces it.

Its tabular output has no percent identity column, so that column stays empty
on the page rather than being filled with something identity-shaped computed a
different way.

**Easy to get backwards:** in phmmer's domain table the `hmm from/to` columns
are coordinates on the *submitted* sequence, because the query became the
profile. `hmmscan` is the other way round. Both are normalised in
`app/search/parsers.py` so the page always shows query coordinates. Verified on
a 198-residue reference: query region 25 to 222, which is 198 of 222 residues,
matching the 89% coverage shown.

## What the profile-HMM layer does

A profile HMM models which positions a family conserves, so it can recognise a
distant relative that no single family member closely resembles. Building one
needs an alignment of genuinely related sequences, which is where most of the
build time goes.

The pipeline clusters the whole export with MMseqs2 at 35% identity and 80%
coverage required on *both* sequences, so a short fragment cannot join a family
by matching one domain of a large protein. Each cluster is then gated: at least
5 members, members outside 70% to 140% of the cluster median length trimmed,
large clusters subsampled to bound MAFFT. What survives is aligned, gated again
on alignment quality, built with `hmmbuild -n`, gated a third time on
match-state count, and pressed into one searchable database.

Searching uses `hmmscan` rather than the faster `hmmsearch`. `hmmsearch`
computes E-values against the size of the target set, which here would be
however many sequences a user happened to paste, so the same protein would
score differently depending on what was submitted alongside it. `hmmscan`
scores against a fixed profile database, so a result is reproducible.

The layer is deliberately partial. On this build 64 profiles cover 2,113 of
2,380 references (88.8%); the other 178 clusters were rejected for having fewer
than five members. The page states that coverage on every profile result, so an
absent profile hit is never read as evidence of absence.

## Why an EC number alone does not define one valid protein family

An EC number names a *reaction*, not an ancestry. Enzymes that catalyse the
same reaction are routinely non-homologous: analogous enzymes that arrived at
the same chemistry independently are common enough to have been surveyed
systematically (Galperin, Walker & Koonin, *Genome Research* 8:779, 1998).

This matters because a profile HMM built from an alignment of unrelated
sequences produces match states that describe nothing real. Its E-values are
then meaningless rather than merely weak, which is worse: a number that looks
like evidence and is not.

The build makes the point concretely. Superoxide dismutase (EC 1.15.1.1)
produced six profiles, and they split by fold, not by EC:

| profile | members | representative | fold |
|---|---|---|---|
| `EXF00003` | 135 | P0C0Q6 Superoxide dismutase [Mn/Fe] | Mn/Fe |
| `EXF00033` | 13 | P28767 Superoxide dismutase [Mn] | Mn/Fe |
| `EXF00057` | 5 | Q5VSB7 Superoxide dismutase [Fe] 2 | Mn/Fe |
| `EXF00005` | 108 | Q9FK60 Superoxide dismutase [Cu-Zn] 3 | Cu/Zn |
| `EXF00030` | 16 | P20379 Superoxide dismutase [Cu-Zn] | Cu/Zn |
| `EXF00035` | 11 | P36214 Superoxide dismutase [Cu-Zn], chloroplastic | Cu/Zn |

Cu/Zn superoxide dismutase is a Greek-key beta-barrel; Mn/Fe superoxide
dismutase is an unrelated fold entirely. One per-EC profile would have aligned
all six groups into a single model of nothing. The same pattern holds elsewhere
in the build: glutathione transferase (2.5.1.18) gives 14 profiles, carbonic
anhydrase (4.2.1.1) gives 10, matching its alpha, beta and gamma classes.

So the ordering is deliberate: cluster on sequence similarity first, attach EC
annotation to the resulting families afterwards, and *report* how homogeneous
each family's annotation is (`ec_purity`) instead of assuming it. References
with no EC at all still contribute to a family.

The human Mn-SOD demo query hit exactly the three Mn/Fe profiles and none of
the three Cu/Zn profiles. That is the outcome the cluster-first design exists
to produce, and it is the check worth repeating against the real EnzymeX data.

## What gets displayed to the user

Three tables, one per method, never side by side. Each hit row carries the
reference identifier, its description, its EC number and its source, all looked
up in `metadata.sqlite3` using the identifier the tool itself returned. Then
the numbers each method can honestly supply:

* **blastp**: identity, alignment length, query and subject coverage, E-value,
  bit score.
* **phmmer**: coverage, sequence and best-domain E-values, bit scores, query
  region. No identity column.
* **hmmscan**: consensus EC, EC purity, member count, coverage against the
  profile, E-value, query region.

The framing is as much a part of the output as the numbers. Results are
labelled sequence-comparison evidence, not EC predictions, and the page says
ECPICK, HIT-EC and CLEAN are not run here. Query coverage below 50% is greyed
out, because identity without coverage is meaningless. E-values are stated to
be incomparable across the three tables, since BLAST and phmmer score against
2,380 sequences under different models and hmmscan scores against 64 profiles.
A no-hit result says the reference set holds no detectable relative, not that
the protein is not an enzyme. Low EC purity is flagged as a reason not to read
the consensus EC as a prediction. CSV and JSON exports carry a row for a method
that found nothing, so "searched, found nothing" stays distinguishable from
"never ran".

All 2,380 reference records were re-checked against the MySQL rows they came
from (identifier, EC, source, sequence hash, length) and against the FASTA
deflines: zero mismatches in both directions. Identity, E-value, bit score and
coverage on the page were compared against a standalone `blastp` run and
matched exactly. See [`HANDOFF.md`](../HANDOFF.md).

## What changes once EnzymeX access is granted

Less than it looks. Everything under `app/search/` and `app/references/`, plus
`fasta.py`, `schemas.py` and `jobs.py`, imports no web framework, so the port
is a function call and a template. What genuinely changes:

1. **Confirm the schema first, before anything else.** The column alias table
   in `db.py` was written against the documented column list and verified only
   against a synthetic fixture. Run `enzymex-refbuild inspect` against the real
   copy; if a column resolves to the wrong name that table is the only edit. If
   the real table has no primary key, export from a view that supplies one,
   because identifiers and ordering both depend on it.
2. **Every number in these docs is void.** They all come from a
   2,380-reference build. Cluster thresholds and QC gates were chosen at that
   scale. Expect `ENZYMEX_PROFILE_MIN_MEMBERS` to need raising, and read
   `profile_member_coverage` in the manifest before trusting the profile layer.
3. **Re-run the provenance check on real data** before any UI work: pick hits,
   trace them back to `enzymesdata` rows by `source_pk`, confirm the EC and
   source on the page match the row. That is the check that catches a clean
   pipeline with a wrong mapping.
4. **Execution mode is a real decision.** EnzymeX already schedules ECPICK,
   HIT-EC and CLEAN. If these results are to share a page, run `run_search` as
   another step in that job rather than inside a request.
5. **Reconcile with DIAMOND.** EnzymeX already uses it for similar-protein
   retrieval. It and blastp answer the same question at different sensitivity,
   so present both with the difference labelled or drop one. The
   proof-of-concept comparison quantifies it: DIAMOND's default mode missed the
   most distant positive that `--very-sensitive`, BLAST and HMMER all found.
6. **Settle identifier policy.** References are `EXR<id>` internally. To show
   UniProt accessions instead, the mapping is already stored. Change the
   display, not the internal identifier: the internal one is what keeps
   deflines safe for the tools.
7. **Drop `app/web/`.** The results page is a reference for what to show, not
   markup to lift. The one thing worth preserving is that the three tables stay
   separate.

Longer form in [`integration.md`](integration.md).
