#!/usr/bin/env bash
# Two HMMER approaches over the same EC 1.1.1.1 reference set:
#
#   A. phmmer    - single sequence query vs. the 20 reference sequences
#   B. hmmbuild  - align the 20 references, build one profile HMM
#      hmmsearch - search that profile against the query sequences
set -euo pipefail

REF=data/raw/reference_ec_1_1_1_1.fasta
QUERIES=data/raw/queries_all.fasta
PROC=data/processed
OUT=results/hmmer

mkdir -p "$PROC" "$OUT"

# ---------------------------------------------------------------- A. phmmer
# Each query sequence is turned into a one-sequence profile internally and
# searched against the reference FASTA, which acts as the target database.
echo "[hmmer] phmmer: queries vs. 20 reference sequences"
phmmer \
  --tblout "$OUT/phmmer_tblout.txt" \
  --domtblout "$OUT/phmmer_domtblout.txt" \
  -o "$OUT/phmmer_full.txt" \
  "$QUERIES" "$REF"

# -------------------------------------------------- B. profile HMM pipeline
echo "[hmmer] mafft: aligning the 20 reference sequences"
mafft --auto --reorder "$REF" \
  > "$PROC/reference_aligned.afa" \
  2> "$OUT/mafft.log"

echo "[hmmer] hmmbuild: building profile HMM from the alignment"
hmmbuild \
  -n EC_1_1_1_1_reference \
  -o "$OUT/hmmbuild.log" \
  "$PROC/ec_1_1_1_1.hmm" \
  "$PROC/reference_aligned.afa"

echo "[hmmer] hmmsearch: profile vs. query sequences"
hmmsearch \
  --tblout "$OUT/hmmsearch_tblout.txt" \
  --domtblout "$OUT/hmmsearch_domtblout.txt" \
  -o "$OUT/hmmsearch_full.txt" \
  "$PROC/ec_1_1_1_1.hmm" "$QUERIES"

# HMMER stamps the absolute working directory into a "# Current dir:" header.
# Strip it from the committed tables so outputs are machine-independent.
sed -i '/^# Current dir:/d' \
  "$OUT/phmmer_tblout.txt" "$OUT/phmmer_domtblout.txt" \
  "$OUT/hmmsearch_tblout.txt" "$OUT/hmmsearch_domtblout.txt"

echo "[hmmer] done. Outputs in $OUT"
