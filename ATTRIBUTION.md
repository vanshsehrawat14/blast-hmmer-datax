# Attribution and provenance

Everything used to produce the results in this repository: software versions,
data sources, exact commands, citations, and licenses.

Two things are recorded here: the **test server** (`app/`, searching
references exported from a copy of the EnzymeX `enzymesdata` table) and the
**proof of concept** (`scripts/0*`, the original EC 1.1.1.1 comparison).

---

## Software

### Search and alignment tools

| Tool | Version | Purpose | License | Source |
|---|---|---|---|---|
| NCBI BLAST+ | 2.16.0+ (build Mar 28 2025) | `makeblastdb`, `blastp` | Public domain (US Government work) | https://blast.ncbi.nlm.nih.gov/ |
| HMMER | 3.4 (Aug 2023) | `phmmer`, `hmmbuild`, `hmmpress`, `hmmscan`, `hmmsearch` | BSD 3-Clause | http://hmmer.org/ |
| MAFFT | 7.526 (2024/Apr/26) | multiple sequence alignment for profile building | BSD 3-Clause | https://mafft.cbrc.jp/alignment/software/ |
| MMseqs2 | 18-8cc5c | clustering references into homologous families | MIT | https://github.com/soedinglab/MMseqs2 |
| DIAMOND | 2.2.4 | optional fast protein aligner (proof of concept only) | GPL-3.0-or-later | https://github.com/bbuchfink/diamond |

### Application

| Package | Version | Purpose | License |
|---|---|---|---|
| Python | 3.12.13 | everything | PSF License |
| FastAPI | 0.121.2 | web framework | MIT |
| Uvicorn | 0.41.0 | ASGI server | BSD 3-Clause |
| Jinja2 | 3.1.6 | server-rendered templates | BSD 3-Clause |
| Pydantic | 2.13.1 | the normalized result model | MIT |
| pydantic-settings | 2.13.0 | environment-driven configuration | MIT |
| PyMySQL | 1.1.2 | read-only access to the copied database | MIT |
| cryptography | 46.0.4 | MySQL 8 `caching_sha2_password` auth | Apache 2.0 / BSD 3-Clause |
| python-multipart | 0.0.20 | FASTA file upload | Apache 2.0 |
| requests | 2.32.5 | UniProt REST client (proof of concept, dev fixture) | Apache 2.0 |
| pandas | 2.2.3 | declared dependency of the proof of concept | BSD 3-Clause |
| pytest | 8.4.2 | tests | MIT |
| httpx | 0.28.1 | test client | BSD 3-Clause |
| micromamba | 2.8.1 | environment management | BSD 3-Clause |

Conda packages were installed from the `conda-forge` and `bioconda` channels;
the resolved BLAST build was `blast-2.16.0-h66d330f_5`, the MMseqs2 build
`mmseqs2-18.8cc5c-hd6d6fdc_0`, and the DIAMOND build
`diamond-2.2.4-he361c42_0`. License fields above were read from the installed
packages' metadata, not assumed.

Versions above were captured on the run recorded in this repository and can be
re-printed at any time with `make versions`. The reference build additionally
records the version of every tool it invoked in
`var/reference/build_manifest.json`.

### Licensing note

BLAST+ (public domain), HMMER (BSD), and MAFFT (BSD) impose nothing beyond
attribution. **DIAMOND is GPL-3.0-or-later** — more restrictive. Running it as
a separate executable and reading its output is ordinary use; bundling or
linking it into distributed software triggers GPL obligations that need review.
This is one reason DIAMOND is optional here, not a required dependency.

### Host environment

| | |
|---|---|
| Host OS | Windows 11 Home (10.0.26200) |
| Execution environment | WSL2, Ubuntu 24.04.3 LTS |
| Environment manager | micromamba 2.8.1, environment `blast-hmmer-datax` |
| Environment definition | [`environment.yml`](environment.yml) |

---

## Data sources

### Copied EnzymeX `enzymesdata` table (test server)

The test server's references are exported from a **copy** of the EnzymeX MySQL
database. That data is **not** in this repository, is not redistributed by it,
and is not packaged into the Docker image: `var/` is gitignored in full and the
image mounts artifacts from a volume.

Provenance travels with each build instead. `var/reference/build_manifest.json`
records the source host, database and table names, the row count read, the
count exported, every skip reason, the tool versions used and a SHA-256 for
each artifact. `metadata.sqlite3` keeps `source_pk` for every reference, plus a
`reference_duplicate` row for every additional `enzymesdata` record that shared
an identical sequence, so any hit traces back to the exact rows it represents.

The handoff materials variously describe `enzymesdata` as drawing from
Swiss-Prot, PDB, KEGG and, in some documents, TrEMBL. Anyone displaying those
sequences or annotations downstream inherits the attribution requirements of
whichever source a record came from; the
`source` column is preserved through the export for exactly that reason.

Development was done against a **synthetic fixture**, not real EnzymeX data —
see "Development fixture" below.

### UniProt (proof of concept, and the development fixture)

**UniProt** — UniProtKB/Swiss-Prot (reviewed, manually curated entries).

| | |
|---|---|
| API endpoint | `https://rest.uniprot.org/uniprotkb/search` |
| Retrieval date | 2026-07-20 |
| Entries retrieved | 27 (20 reference, 5 positive queries, 2 negative controls) |
| License | Creative Commons Attribution 4.0 (CC BY 4.0) |
| Terms of use | https://www.uniprot.org/help/license |

UniProt data is redistributed here under CC BY 4.0. Any downstream display of
these sequences or their annotations must credit UniProt.

### Exact API queries

Reference set and positive queries:

```
(ec:1.1.1.1) AND (reviewed:true) AND (fragment:false)
```

Negative controls:

```
(ec:3.2.1.1) AND (reviewed:true) AND (fragment:false)
(ec:2.7.1.1) AND (reviewed:true) AND (fragment:false)
```

Requested fields:
`accession,protein_name,organism_name,ec,length,reviewed,sequence`

### Selection rule

Results are sorted by accession, deduplicated by accession and by identical
sequence, and filtered to entries where the target EC is actually present in
the annotation. The first 20 become the reference set and the next 5 become
positive queries. Each negative control is the lowest-accession reviewed entry
for its EC that is not also annotated EC 1.1.1.1 and does not already appear in
another set.

This makes the download deterministic for a given UniProt release. The exact
accessions used are recorded in
[`data/raw/provenance.json`](data/raw/provenance.json) and
[`data/raw/metadata.tsv`](data/raw/metadata.tsv).

### Reference set

Custom 20-sequence set built for this project. No external or pre-existing
sequence database (nr, Swiss-Prot as a whole, Pfam, etc.) was downloaded or
searched — every proof-of-concept search runs against these 20 sequences.

### Development fixture

`scripts/dev_seed_fixture.py` builds a local MySQL stand-in for the copied
`enzymesdata` table so the export, clustering and profile pipeline could be
developed and verified without EnzymeX access. It is **development tooling
only**, not part of the deployment path, and it contains no EnzymeX data.

It downloads reviewed UniProtKB entries for twelve EC numbers — 1.15.1.1,
4.2.1.1, 1.1.1.1, 3.2.1.1, 3.4.21.4, 1.11.1.6, 2.5.1.18, 5.3.1.9, 6.1.1.1,
2.7.1.1, 3.1.1.7 and 1.1.1.37 — chosen because several of them (superoxide
dismutase, carbonic anhydrase, glutathione transferase) are served by more
than one unrelated protein family, which is precisely the case the clustering
design has to handle. Source labels (Swiss-Prot / TrEMBL / PDB / KEGG) are
assigned synthetically to simulate the provenance mix; every sequence is
genuinely Swiss-Prot. Ten deliberately damaged rows are added to exercise the
export's validation and skip reporting.

The source-policy build reported in the README is 2,677 fixture rows → 1,574
Swiss-Prot/PDB-labelled references after validation, source selection and
exact-sequence deduplication. Retrieval date 2026-07-26; the same CC BY 4.0
terms apply.

---

## Exact commands — test server

Tool invocations built by `app/`. Paths shown relative to
`ENZYMEX_REFERENCE_DIR` (default `var/reference/`).

### Reference export

```bash
enzymex-refbuild export      # or: make export
```

Reads the copied table with `SET SESSION TRANSACTION READ ONLY`, ordered by
primary key through a server-side cursor. No tool invocation.

### BLAST database — `app/references/blast_build.py`

```bash
makeblastdb \
  -in var/reference/references.fasta \
  -dbtype prot \
  -title "EnzymeX copied enzymesdata references (build <id>)" \
  -out var/reference/blastdb/references
```

`-parse_seqids` is deliberately **not** used here (unlike the proof of
concept): it rewrites deflines into `sp|ACC|` form, which then has to be
stripped from every result row, and nothing needs `blastdbcmd` retrieval.

### Profile HMMs — `app/references/cluster.py`, `app/references/hmmer_build.py`

```bash
mmseqs easy-cluster \
  var/reference/references.fasta var/reference/work/clusters var/reference/work/mmseqs_tmp \
  --min-seq-id 0.35 -c 0.80 --cov-mode 0 --cluster-mode 0 --threads 4 -v 1

mafft --auto --anysymbol --reorder --quiet --thread 4 members.fasta > aligned.afa

hmmbuild -n EXF00001 --amino --cpu 4 -o hmmbuild.log EXF00001.hmm aligned.afa

hmmpress -f var/reference/profiles/profiles.hmm
```

`--cov-mode 0` requires 80% coverage on both sequences; `--cluster-mode 0` is
greedy set cover, so every member meets the threshold against its
representative. `--anysymbol` stops MAFFT remapping U/O/B/Z/J.

### Searches — `app/search/blast.py`, `app/search/hmmer.py`

```bash
blastp \
  -query <job>/query.fasta \
  -db var/reference/blastdb/references \
  -outfmt "6 qseqid sseqid pident length qstart qend sstart send qlen slen evalue bitscore qcovs" \
  -out <job>/blastp_hits.tsv \
  -evalue 0.001 -max_target_seqs 500 -num_threads 2 -comp_based_stats 2

phmmer \
  --tblout <job>/phmmer_tblout.txt --domtblout <job>/phmmer_domtblout.txt \
  -o <job>/phmmer_full.txt -E 0.001 --cpu 2 \
  <job>/query.fasta var/reference/references.fasta

hmmscan \
  --tblout <job>/hmmscan_tblout.txt --domtblout <job>/hmmscan_domtblout.txt \
  -o <job>/hmmscan_full.txt -E 0.001 --cpu 2 \
  var/reference/profiles/profiles.hmm <job>/query.fasta
```

Every one of these runs through `app/search/subprocess_utils.run_tool`:
argument list, `shell=False`, mandatory timeout, process-group kill on expiry,
output to files. Query deflines contain only server-generated `Q1`, `Q2`, …
identifiers — no user text reaches a command line. `-max_target_seqs 500` is a
search-time cutoff, not a top-N filter; the displayed list is truncated to
`ENZYMEX_MAX_HITS_PER_QUERY` by our own ranking afterwards.

Scoring matrix and gap penalties are `blastp` defaults: **BLOSUM62**, gap open
11, gap extend 1.

---

## Exact commands — proof of concept

Full definitions are in `scripts/`; these are the tool invocations.

### BLAST — `scripts/02_run_blast.sh`

```bash
makeblastdb \
  -in data/raw/reference_ec_1_1_1_1.fasta \
  -dbtype prot \
  -parse_seqids \
  -title "EC 1.1.1.1 reviewed reference set (20 sequences, UniProtKB/Swiss-Prot)" \
  -out data/processed/blastdb/ec_1_1_1_1

blastp \
  -query data/raw/queries_all.fasta \
  -db data/processed/blastdb/ec_1_1_1_1 \
  -evalue 10 \
  -max_target_seqs 20 \
  -num_threads 1 \
  -outfmt "6 qseqid sseqid pident length qcovs evalue bitscore stitle" \
  -out results/blast/blastp_hits.tsv
```

The same `blastp` command is also run with no `-outfmt` (default pairwise
output) and with `-outfmt "7 ..."` (tabular with field-name comments).

Key parameters:

- `-dbtype prot` — protein database.
- `-parse_seqids` — parse sequence IDs so subjects are individually
  addressable. Note this rewrites UniProt-style IDs as `sp|ACCESSION|`.
- `-evalue 10` — BLAST's default reporting threshold, kept deliberately
  permissive so that weak negative-control hits are visible rather than
  silently filtered.
- `-max_target_seqs 20` — the whole database, so nothing is truncated.
- `-num_threads 1` — determinism and portability.
- Scoring matrix and gap penalties are `blastp` defaults: **BLOSUM62**,
  gap open 11, gap extend 1.

### HMMER — `scripts/03_run_hmmer.sh`

```bash
# A. single-sequence search
phmmer \
  --tblout results/hmmer/phmmer_tblout.txt \
  --domtblout results/hmmer/phmmer_domtblout.txt \
  -o results/hmmer/phmmer_full.txt \
  data/raw/queries_all.fasta data/raw/reference_ec_1_1_1_1.fasta

# B. profile pipeline
mafft --auto --reorder data/raw/reference_ec_1_1_1_1.fasta \
  > data/processed/reference_aligned.afa

hmmbuild \
  -n EC_1_1_1_1_reference \
  -o results/hmmer/hmmbuild.log \
  data/processed/ec_1_1_1_1.hmm \
  data/processed/reference_aligned.afa

hmmsearch \
  --tblout results/hmmer/hmmsearch_tblout.txt \
  --domtblout results/hmmer/hmmsearch_domtblout.txt \
  -o results/hmmer/hmmsearch_full.txt \
  data/processed/ec_1_1_1_1.hmm data/raw/queries_all.fasta
```

Key parameters:

- `mafft --auto` selected the **L-INS-i** strategy for this input (reported in
  `results/hmmer/mafft.log`). `--reorder` sorts output by similarity.
- `hmmbuild -n` names the profile so it is identifiable in the output tables.
  The resulting model has **427 match states** built from **20 sequences**.
- No `-E` / `-T` thresholds are set, so HMMER's defaults apply (report
  E-value ≤ 10, inclusion E-value ≤ 0.01). As with BLAST, thresholds were left
  permissive so negative-control behaviour is observable.
- `--tblout` gives one line per sequence hit; `--domtblout` gives one line per
  domain hit with alignment coordinates, which the parser uses for coverage.

### DIAMOND (optional) — `scripts/05_run_diamond.sh`

```bash
diamond makedb \
  --in data/raw/reference_ec_1_1_1_1.fasta \
  -d data/processed/diamonddb/ec_1_1_1_1 \
  --threads 1

# primary: the mode closest to blastp
diamond blastp \
  -q data/raw/queries_all.fasta \
  -d data/processed/diamonddb/ec_1_1_1_1 \
  --very-sensitive \
  -e 10 \
  --max-target-seqs 20 \
  --threads 1 \
  -f 6 qseqid sseqid pident length qcovhsp evalue bitscore stitle \
  -o results/diamond/diamond_hits.tsv

# same command without --very-sensitive, written to diamond_hits_default.tsv
```

Key parameters:

- `--very-sensitive` — used for the primary DIAMOND result because DIAMOND's
  default mode is tuned for large databases and high-identity matches. On this
  reference set the default mode fails to find the most distant positive query
  (`O07737`), so both modes are recorded.
- `-e 10`, `--max-target-seqs 20` — matched to the BLAST run for comparability.
- `--threads 1` — determinism.
- `qcovhsp` — DIAMOND does **not** implement BLAST's `qcovs`, so the coverage
  column is per-HSP coverage and is not identical in meaning to the BLAST
  coverage column.
- `-f 0` is additionally used to produce human-readable pairwise output.

### Data download and parsing

```bash
python scripts/01_download_uniprot.py
python scripts/04_parse_results.py
python tests/test_pipeline.py
```

---

## Citations

**NCBI BLAST+**

> Camacho C., Coulouris G., Avagyan V., Ma N., Papadopoulos J., Bealer K.,
> Madden T.L. (2009). *BLAST+: architecture and applications.*
> BMC Bioinformatics 10:421. https://doi.org/10.1186/1471-2105-10-421

> Altschul S.F., Gish W., Miller W., Myers E.W., Lipman D.J. (1990).
> *Basic local alignment search tool.* Journal of Molecular Biology
> 215(3):403–410. https://doi.org/10.1016/S0022-2836(05)80360-2

**HMMER**

> Eddy S.R. (2011). *Accelerated Profile HMM Searches.* PLoS Computational
> Biology 7(10):e1002195. https://doi.org/10.1371/journal.pcbi.1002195

> Potter S.C., Luciani A., Eddy S.R., Park Y., Lopez R., Finn R.D. (2018).
> *HMMER web server: 2018 update.* Nucleic Acids Research 46(W1):W200–W204.
> https://doi.org/10.1093/nar/gky448

**MAFFT**

> Katoh K., Standley D.M. (2013). *MAFFT Multiple Sequence Alignment Software
> Version 7: Improvements in Performance and Usability.* Molecular Biology and
> Evolution 30(4):772–780. https://doi.org/10.1093/molbev/mst010

**MMseqs2**

> Steinegger M., Söding J. (2017). *MMseqs2 enables sensitive protein sequence
> searching for the analysis of massive data sets.* Nature Biotechnology
> 35:1026–1028. https://doi.org/10.1038/nbt.3988

**Design decisions in `docs/science.md`**

> Galperin M.Y., Walker D.R., Koonin E.V. (1998). *Analogous enzymes:
> independent inventions in enzyme evolution.* Genome Research 8(8):779–790.
> https://doi.org/10.1101/gr.8.8.779
> — why profile HMMs are not built per EC number.

> Rost B. (1999). *Twilight zone of protein sequence alignments.* Protein
> Engineering 12(2):85–94. https://doi.org/10.1093/protein/12.2.85
> — why the clustering identity threshold is 35%, not lower.

> Shah N., Nute M.G., Warnow T., Pop M. (2019). *Misunderstood parameter of
> NCBI BLAST impacts the correctness of bioinformatics workflows.*
> Bioinformatics 35(9):1786–1788. https://doi.org/10.1093/bioinformatics/bty833
> — why `-max_target_seqs` is not used as a top-N filter.

> Yu Y.-K., Wootton J.C., Altschul S.F. (2003). *The compositional adjustment
> of amino acid substitution matrices.* PNAS 100(26):15688–15693.
> https://doi.org/10.1073/pnas.2533904100
> — the basis of `-comp_based_stats 2`.

> Eddy S.R. (2008). *A probabilistic model of local sequence alignment that
> simplifies statistical significance estimation.* PLoS Computational Biology
> 4(5):e1000069. https://doi.org/10.1371/journal.pcbi.1000069
> — the E-value theory behind the HMMER numbers, and why the size of the
> searched set matters (hence `hmmscan` rather than `hmmsearch`).

**DIAMOND** (optional method)

> Buchfink B., Reuter K., Drost H.-G. (2021). *Sensitive protein alignments at
> tree-of-life scale using DIAMOND.* Nature Methods 18:366–368.
> https://doi.org/10.1038/s41592-021-01101-x

> Buchfink B., Xie C., Huson D.H. (2015). *Fast and sensitive protein alignment
> using DIAMOND.* Nature Methods 12:59–60.
> https://doi.org/10.1038/nmeth.3176

**UniProt**

> The UniProt Consortium (2025). *UniProt: the Universal Protein
> Knowledgebase in 2025.* Nucleic Acids Research 53(D1):D609–D617.
> https://doi.org/10.1093/nar/gkae1010

**Enzyme Commission nomenclature**

> Nomenclature Committee of the International Union of Biochemistry and
> Molecular Biology (NC-IUBMB). *Enzyme Nomenclature.*
> https://iubmb.qmul.ac.uk/enzyme/

---

## Scope notes

**DIAMOND** was mentioned as a related high-speed sequence aligner. It was
added as an **optional** comparison after the required BLAST and HMMER work was
complete, and it is not part of the required workflow. The test server does not
use it at all; the proof of concept runs without it
(`make poc-blast poc-hmmer poc-parse`).

**No claim is made in this repository regarding DIAMOND's authorship.** It is
attributed above to its published authors (Buchfink, Xie, Huson 2015;
Buchfink, Reuter, Drost 2021) as recorded in the peer-reviewed literature.

**No performance claim is made about DIAMOND.** Its advantage is throughput on
databases far larger than the 20-sequence proof-of-concept reference set, where
every tool completes effectively instantly. No DIAMOND timings were measured
and none should be inferred from this repository.

**Runtime figures for the test server are measurements, not benchmarks.** The
numbers in the README and `docs/testing.md` describe one build (1,574
references, 47 profiles) on one machine (WSL2 / Ubuntu 24.04). They are there
so the deployment guidance is grounded in something, not to compare tools.

**EnzymeX itself is not modified.** No code in this repository was taken from,
written to, or deployed to the EnzymeX codebase or production server. The
public `datax-lab/enzymex` repository was not reachable during this work
(HTTP 404 as of 2026-07-26), so compatibility expectations rest on the
documented `enzymesdata` column list and the public EnzymeX site, and must be
re-verified once repository access is granted.
