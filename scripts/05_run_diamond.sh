#!/usr/bin/env bash
# Optional fourth method: DIAMOND, over the same reference set and queries.
#
# DIAMOND is a fast protein aligner designed as a drop-in alternative to
# blastp for large databases. It is run here in two modes on purpose:
#
#   --very-sensitive  closest in behaviour to blastp, used as the primary
#                     DIAMOND result
#   (default)         DIAMOND's fast mode, kept to show the speed/sensitivity
#                     trade-off explicitly
#
# On this reference set the default mode misses the most distant positive
# query, which is the single most useful thing DIAMOND demonstrates here.
set -euo pipefail

REF=data/raw/reference_ec_1_1_1_1.fasta
QUERIES=data/raw/queries_all.fasta
DB_DIR=data/processed/diamonddb
DB=$DB_DIR/ec_1_1_1_1
OUT=results/diamond

mkdir -p "$DB_DIR" "$OUT"

# DIAMOND does not support BLAST's qcovs (coverage summed over all HSPs for a
# subject). qcovhsp is per-HSP coverage, so it is not strictly the same number
# as the qcovs column in the BLAST output.
FIELDS="qseqid sseqid pident length qcovhsp evalue bitscore stitle"

echo "[diamond] building database"
diamond makedb \
  --in "$REF" \
  -d "$DB" \
  --threads 1 \
  2> "$OUT/makedb.log"

echo "[diamond] blastp --very-sensitive (primary)"
diamond blastp \
  -q "$QUERIES" \
  -d "$DB" \
  --very-sensitive \
  -e 10 \
  --max-target-seqs 20 \
  --threads 1 \
  -f 6 $FIELDS \
  -o "$OUT/diamond_hits.tsv" \
  2> "$OUT/diamond_very_sensitive.log"

echo "[diamond] blastp default mode (for comparison)"
diamond blastp \
  -q "$QUERIES" \
  -d "$DB" \
  -e 10 \
  --max-target-seqs 20 \
  --threads 1 \
  -f 6 $FIELDS \
  -o "$OUT/diamond_hits_default.tsv" \
  2> "$OUT/diamond_default.log"

echo "[diamond] pairwise output (human readable)"
diamond blastp \
  -q "$QUERIES" \
  -d "$DB" \
  --very-sensitive \
  -e 10 \
  --max-target-seqs 20 \
  --threads 1 \
  -f 0 \
  -o "$OUT/diamond_pairwise.txt" \
  2>> "$OUT/diamond_very_sensitive.log"

echo "[diamond] done: $(wc -l < "$OUT/diamond_hits.tsv") rows (very-sensitive), \
$(wc -l < "$OUT/diamond_hits_default.tsv") rows (default)"
