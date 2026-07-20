#!/usr/bin/env bash
# Build a local protein BLAST database from the 20 EC 1.1.1.1 reference
# sequences and search it with the held-out positive and negative queries.
set -euo pipefail

REF=data/raw/reference_ec_1_1_1_1.fasta
QUERIES=data/raw/queries_all.fasta
DB_DIR=data/processed/blastdb
DB=$DB_DIR/ec_1_1_1_1
OUT=results/blast

mkdir -p "$DB_DIR" "$OUT"

echo "[blast] building database"
makeblastdb \
  -in "$REF" \
  -dbtype prot \
  -parse_seqids \
  -title "EC 1.1.1.1 reviewed reference set (20 sequences, UniProtKB/Swiss-Prot)" \
  -out "$DB" \
  > "$OUT/makeblastdb.log"

# makeblastdb echoes the absolute output path; rewrite it to the repo-relative
# form so the log is machine-independent.
sed -i "s#$PWD/##g" "$OUT/makeblastdb.log"

echo "[blast] blastp -> pairwise (human readable)"
blastp \
  -query "$QUERIES" \
  -db "$DB" \
  -evalue 10 \
  -max_target_seqs 20 \
  -num_threads 1 \
  -out "$OUT/blastp_pairwise.txt"

echo "[blast] blastp -> tabular"
# qcovs = query coverage per subject, as a percentage of the query length.
FIELDS="qseqid sseqid pident length qcovs evalue bitscore stitle"

blastp \
  -query "$QUERIES" \
  -db "$DB" \
  -evalue 10 \
  -max_target_seqs 20 \
  -num_threads 1 \
  -outfmt "6 $FIELDS" \
  -out "$OUT/blastp_hits.tsv"

# outfmt 7 is the same table with '#' comment lines naming the fields,
# which makes the raw file readable without consulting the command line.
blastp \
  -query "$QUERIES" \
  -db "$DB" \
  -evalue 10 \
  -max_target_seqs 20 \
  -num_threads 1 \
  -outfmt "7 $FIELDS" \
  -out "$OUT/blastp_hits_commented.tsv"

echo "[blast] done: $(wc -l < "$OUT/blastp_hits.tsv") hit rows in $OUT/blastp_hits.tsv"
