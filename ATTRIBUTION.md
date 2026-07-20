# Attribution and provenance

Everything used to produce the results in this repository: software versions,
data sources, exact commands, citations, and licenses.

---

## Software

| Tool | Version | Purpose | License | Source |
|---|---|---|---|---|
| NCBI BLAST+ | 2.16.0+ (build Mar 28 2025) | `makeblastdb`, `blastp` | Public domain (US Government work) | https://blast.ncbi.nlm.nih.gov/ |
| HMMER | 3.4 (Aug 2023) | `phmmer`, `hmmbuild`, `hmmsearch` | BSD 3-Clause | http://hmmer.org/ |
| MAFFT | 7.526 (2024/Apr/26) | multiple sequence alignment | BSD 3-Clause | https://mafft.cbrc.jp/alignment/software/ |
| DIAMOND | 2.2.4 | optional fast protein aligner | GPL-3.0-or-later | https://github.com/bbuchfink/diamond |
| Python | 3.12.13 | download and parsing scripts | PSF License | https://www.python.org/ |
| requests | 2.32.5 | UniProt REST API client | Apache 2.0 | https://requests.readthedocs.io/ |
| pandas | 2.2.3 | declared dependency | BSD 3-Clause | https://pandas.pydata.org/ |
| micromamba | 2.8.1 | environment management | BSD 3-Clause | https://mamba.readthedocs.io/ |

Conda packages were installed from the `conda-forge` and `bioconda` channels;
the resolved BLAST build was `blast-2.16.0-h66d330f_5` and the DIAMOND build
was `diamond-2.2.4-he361c42_0`. License fields above were read from the
installed packages' conda metadata, not assumed.

Versions above were captured on the run recorded in this repository and can be
re-printed at any time with `make versions`.

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

## Data source

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
searched — every search in this repository runs against these 20 sequences.

---

## Exact commands

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
complete, and it is not part of the required workflow — `make blast hmmer parse`
runs without it.

**No claim is made in this repository regarding DIAMOND's authorship.** It is
attributed above to its published authors (Buchfink, Xie, Huson 2015;
Buchfink, Reuter, Drost 2021) as recorded in the peer-reviewed literature.

**No performance claim is made.** DIAMOND's advantage is throughput on
databases far larger than this 20-sequence reference set, where every tool
completes effectively instantly. No timings were measured and none should be
inferred from this repository.
