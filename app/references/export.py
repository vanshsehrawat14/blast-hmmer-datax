"""Export validated protein references from the copied `enzymesdata` table.

Output is deterministic: rows are read in primary-key order, identifiers are
derived from the primary key, and no timestamp or hostname reaches the FASTA.
Running the export twice against an unchanged copy produces byte-identical
artifacts, which is what makes the build id a meaningful version.

Everything that is dropped is counted and written to `skipped.tsv` with a
reason. A silent export is a useless export: on a table mixing Swiss-Prot,
TrEMBL, PDB and KEGG records, the skip report is the first thing that tells
you whether the reference set is what you think it is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.fasta import clean_sequence
from app.references import db as dbmod
from app.references.metadata import connect_write

log = logging.getLogger(__name__)

# EC numbers may be partial ("1.1.-.-") and preliminary ("1.1.1.n5"); both are
# valid IUBMB forms and are kept. Anything else is treated as unannotated.
EC_TOKEN = re.compile(r"^\d+\.(?:\d+|-)\.(?:\d+|-)\.(?:n?\d+|-)$")
_ID_SAFE = re.compile(r"[^A-Za-z0-9]")
_SOURCE_SAFE = re.compile(r"[^a-z0-9]+")


@dataclass
class ExportStats:
    rows_read: int = 0
    exported: int = 0
    skipped: Counter = field(default_factory=Counter)
    duplicate_sequences: int = 0
    duplicate_identifiers: int = 0
    sources: Counter = field(default_factory=Counter)
    ec_present: int = 0
    multi_ec: int = 0
    length_min: int | None = None
    length_max: int | None = None
    length_sum: int = 0

    def as_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "exported": self.exported,
            "skipped_total": sum(self.skipped.values()),
            "skipped_by_reason": dict(sorted(self.skipped.items())),
            "duplicate_sequences_merged": self.duplicate_sequences,
            "duplicate_identifiers_disambiguated": self.duplicate_identifiers,
            "sources": dict(self.sources.most_common()),
            "with_ec_annotation": self.ec_present,
            "multi_ec_records": self.multi_ec,
            "sequence_length": {
                "min": self.length_min,
                "max": self.length_max,
                "mean": round(self.length_sum / self.exported, 1) if self.exported else None,
            },
        }


def normalize_ec(raw: str | None) -> tuple[str | None, int]:
    """Return (normalized EC string, token count).

    Accepts the separators seen in practice — `;`, `,`, whitespace — and
    returns a sorted, deduplicated, semicolon-joined string so that two rows
    listing the same ECs in a different order compare equal.
    """
    if not raw:
        return None, 0
    tokens = [t.strip() for t in re.split(r"[;,\s]+", str(raw)) if t.strip()]
    valid = sorted({t for t in tokens if EC_TOKEN.match(t)})
    return (";".join(valid) if valid else None), len(valid)


def normalize_source(raw: str | None) -> str:
    """Collapse source labels to a small stable vocabulary."""
    if not raw:
        return "unknown"
    s = _SOURCE_SAFE.sub("_", str(raw).strip().lower()).strip("_")
    if not s:
        return "unknown"
    aliases = {
        "sp": "swissprot", "swiss_prot": "swissprot", "uniprotkb_swiss_prot": "swissprot",
        "tr": "trembl", "uniprotkb_trembl": "trembl",
        "pdb": "pdb", "rcsb": "pdb", "kegg": "kegg", "uniprot": "uniprot",
    }
    return aliases.get(s, s)[:32]


def make_ref_id(primary_key) -> str:
    """`EXR<pk>` — stable across rebuilds, safe as a FASTA identifier.

    BLAST and HMMER split a defline at whitespace and BLAST additionally
    reinterprets `|`-delimited identifiers, so the id is restricted to
    alphanumerics. `-parse_seqids` is deliberately not used when building the
    database, which keeps `sseqid` byte-identical to what is written here.
    """
    return "EXR" + _ID_SAFE.sub("_", str(primary_key))[:48]


def _description(row: dict, schema: dbmod.TableSchema) -> str | None:
    parts = []
    for field_name in ("accession", "description"):
        col = schema.column(field_name)
        if col and row.get(col):
            parts.append(str(row[col]).strip())
    text = " ".join(p for p in parts if p)
    # Collapse whitespace so the value renders on one line and stays short
    # enough for a table cell.
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] or None


def export_references(settings: Settings, out_dir: Path | None = None) -> dict:
    """Read `enzymesdata`, validate, deduplicate and write the artifacts."""
    out_dir = out_dir or settings.reference_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = out_dir / "references.fasta"
    skipped_path = out_dir / "skipped.tsv"
    meta_path = out_dir / "metadata.sqlite3"

    conn = dbmod.connect(settings)
    try:
        schema = dbmod.inspect_schema(conn, settings.db_table)
        if not schema.has("sequence"):
            raise ValueError(
                f"{schema.table} has no recognisable sequence column "
                f"(looked for {dbmod.COLUMN_ALIASES['sequence']}); columns are "
                f"{schema.columns}"
            )
        log.info("table %s: %d rows, primary key %s, mapped columns %s",
                 schema.table, schema.row_count, schema.primary_key,
                 sorted(schema.mapping))
        if schema.missing():
            log.warning("columns not found in the copy (exported as null): %s",
                        ", ".join(schema.missing()))

        if meta_path.exists():
            meta_path.unlink()
        store = connect_write(meta_path)
        stats = ExportStats()

        seq_col = schema.column("sequence")
        ec_col = schema.column("ec")
        src_col = schema.column("source")

        # sha256 -> ref_id of the first row carrying that exact sequence.
        by_hash: dict[str, str] = {}
        used_ids: set[str] = set()
        skipped_rows: list[tuple[str, str, str]] = []

        with fasta_path.open("w", encoding="ascii", newline="\n") as fh:
            for row in dbmod.iter_rows(conn, schema, limit=settings.export_limit):
                stats.rows_read += 1
                pk = row[schema.primary_key]

                cleaned = clean_sequence(
                    row.get(seq_col),
                    min_length=settings.min_sequence_length,
                    max_length=settings.max_reference_length,
                    max_ambiguous_fraction=settings.max_ambiguous_fraction,
                )
                if cleaned.sequence is None:
                    reason = (cleaned.reason or "invalid").split(":")[0]
                    stats.skipped[reason] += 1
                    if len(skipped_rows) < 100_000:
                        skipped_rows.append((str(pk), reason, cleaned.reason or ""))
                    continue

                seq = cleaned.sequence
                digest = hashlib.sha256(seq.encode("ascii")).hexdigest()
                ec, ec_count = normalize_ec(row.get(ec_col) if ec_col else None)
                source = normalize_source(row.get(src_col) if src_col else None)

                # Identical sequences add nothing to a search database — BLAST
                # and HMMER would return the same alignment n times and the
                # E-value would be computed against an inflated database size.
                # The row is kept as provenance instead of being dropped.
                if digest in by_hash:
                    stats.duplicate_sequences += 1
                    stats.skipped["duplicate_sequence"] += 1
                    store.execute(
                        "INSERT INTO reference_duplicate "
                        "(ref_id, source_pk, description, ec, source) VALUES (?,?,?,?,?)",
                        (by_hash[digest], str(pk), _description(row, schema), ec, source),
                    )
                    continue

                ref_id = make_ref_id(pk)
                if ref_id in used_ids:
                    stats.duplicate_identifiers += 1
                    suffix = 2
                    while f"{ref_id}_{suffix}" in used_ids:
                        suffix += 1
                    ref_id = f"{ref_id}_{suffix}"
                used_ids.add(ref_id)
                by_hash[digest] = ref_id

                fh.write(f">{ref_id} EC={ec or 'NA'} src={source}\n")
                for i in range(0, len(seq), 60):
                    fh.write(seq[i:i + 60] + "\n")

                store.execute(
                    "INSERT INTO reference (ref_id, source_pk, description, ec, source, "
                    "length, sequence_sha256, motif, active_site, binding_site, interpretation) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ref_id, str(pk), _description(row, schema), ec, source,
                        len(seq), digest,
                        _opt(row, schema, "motif"), _opt(row, schema, "active"),
                        _opt(row, schema, "binding"), _opt(row, schema, "interpretation"),
                    ),
                )

                stats.exported += 1
                stats.sources[source] += 1
                stats.length_sum += len(seq)
                stats.length_min = len(seq) if stats.length_min is None else min(stats.length_min, len(seq))
                stats.length_max = len(seq) if stats.length_max is None else max(stats.length_max, len(seq))
                if ec:
                    stats.ec_present += 1
                if ec_count > 1:
                    stats.multi_ec += 1

                if stats.rows_read % 50_000 == 0:
                    store.commit()
                    log.info("… %d rows read, %d exported", stats.rows_read, stats.exported)

        with skipped_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("source_pk\treason\tdetail\n")
            for pk, reason, detail in skipped_rows:
                fh.write(f"{pk}\t{reason}\t{detail}\n")

        store.commit()
        store.close()
    finally:
        conn.close()

    if stats.exported == 0:
        raise RuntimeError(
            "No sequences survived validation. Check the skip report at "
            f"{skipped_path.name} and the min/max length settings."
        )

    result = {
        "table": schema.table,
        "database": settings.db_name,
        "primary_key": schema.primary_key,
        "columns_mapped": schema.mapping,
        "columns_missing": schema.missing(),
        "fasta_sha256": sha256_file(fasta_path),
        **stats.as_dict(),
    }
    (out_dir / "export_stats.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    log.info("exported %d/%d rows to %s", stats.exported, stats.rows_read, fasta_path.name)
    return result


def _opt(row: dict, schema: dbmod.TableSchema, field_name: str) -> str | None:
    col = schema.column(field_name)
    if not col:
        return None
    value = row.get(col)
    if value is None:
        return None
    return str(value)[:2000] or None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
