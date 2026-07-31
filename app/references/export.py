"""Export validated protein references from the copied `enzymesdata` table.

Output is deterministic: rows are read in primary-key order, identifiers are
derived from the primary key, and no timestamp or hostname reaches the FASTA.
Running the export twice against an unchanged copy produces byte-identical
artifacts, which is what makes the build id a meaningful version.

Everything that is dropped is counted and written to `skipped.tsv` with a
reason. A silent export is a useless export: handoff materials variously
describe Swiss-Prot, PDB, KEGG and TrEMBL rows, so the skip report is the first
thing that tells you whether the reference set is what you think it is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
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
_SOURCE_PRIORITY = {"swissprot": 0, "pdb": 1}


@dataclass
class ExportStats:
    rows_read: int = 0
    exported: int = 0
    skipped: Counter = field(default_factory=Counter)
    duplicate_sequences: int = 0
    duplicate_identifiers: int = 0
    sources: Counter = field(default_factory=Counter)
    selected_sources: Counter = field(default_factory=Counter)
    excluded_sources: Counter = field(default_factory=Counter)
    duplicate_sources: Counter = field(default_factory=Counter)
    canonical_source_promotions: int = 0
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
            "selected_rows_by_source": dict(self.selected_sources.most_common()),
            "excluded_rows_by_source": dict(self.excluded_sources.most_common()),
            "duplicate_rows_by_source": dict(self.duplicate_sources.most_common()),
            "canonical_source_promotions": self.canonical_source_promotions,
            "with_ec_annotation": self.ec_present,
            "multi_ec_records": self.multi_ec,
            "sequence_length": {
                "min": self.length_min,
                "max": self.length_max,
                "mean": round(self.length_sum / self.exported, 1) if self.exported else None,
            },
        }


@dataclass(frozen=True)
class PreparedReference:
    source_pk: str
    sequence: str
    digest: str
    ec: str | None
    ec_count: int
    source: str
    description: str | None


@dataclass(frozen=True)
class CanonicalReference:
    source_pk: str
    source: str
    ordinal: int


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
    selected_sources = settings.selected_reference_sources
    allowed_sources = frozenset(selected_sources)
    out_dir = out_dir or settings.reference_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = out_dir / "references.fasta"
    skipped_path = out_dir / "skipped.tsv"
    meta_path = out_dir / "metadata.sqlite3"
    staging_ctx = tempfile.TemporaryDirectory(prefix=".export-", dir=out_dir)
    staging = Path(staging_ctx.name)
    staged_fasta = staging / fasta_path.name
    staged_skipped = staging / skipped_path.name
    staged_meta = staging / meta_path.name
    store = None
    conn = None

    try:
        conn = dbmod.connect(settings)
        schema = dbmod.inspect_schema(conn, settings.db_table)
        if not schema.has("sequence"):
            raise ValueError(
                f"{schema.table} has no recognisable sequence column "
                f"(looked for {dbmod.COLUMN_ALIASES['sequence']}); columns are "
                f"{schema.columns}"
            )
        if not schema.has("source"):
            raise ValueError(
                f"{schema.table} has no recognisable source column "
                f"(looked for {dbmod.COLUMN_ALIASES['source']}); one is required "
                f"to select {', '.join(selected_sources)} references"
            )
        if schema.engine and schema.engine.lower() != "innodb":
            raise ValueError(
                f"{schema.table} uses non-transactional engine {schema.engine}; "
                "the two export scans require InnoDB or a view over transactional tables"
            )
        if schema.engine is None:
            log.warning(
                "could not verify the storage engine for %s; if this is a view, "
                "confirm its source tables are transactional",
                schema.table,
            )
        log.info("table %s: %d rows, primary key %s, mapped columns %s",
                 schema.table, schema.row_count, schema.primary_key,
                 sorted(schema.mapping))
        if schema.missing():
            log.warning("columns not found in the copy (exported as null): %s",
                        ", ".join(schema.missing()))

        dbmod.begin_consistent_snapshot(conn)
        stats = ExportStats()
        winners: dict[str, CanonicalReference] = {}
        skipped_rows: list[tuple[str, str, str]] = []

        for ordinal, row in enumerate(
            dbmod.iter_rows(conn, schema, limit=settings.export_limit)
        ):
            stats.rows_read += 1
            prepared, reason, detail = _prepare_reference(
                row, schema, settings, allowed_sources
            )
            if prepared is None:
                stats.skipped[reason or "invalid"] += 1
                if reason == "source_not_selected":
                    stats.excluded_sources[detail] += 1
                    detail = (
                        f"source={detail}; selected={','.join(selected_sources)}"
                    )
                if len(skipped_rows) < 100_000:
                    skipped_rows.append((
                        str(row[schema.primary_key]), reason or "invalid", detail
                    ))
                continue

            stats.selected_sources[prepared.source] += 1
            current = winners.get(prepared.digest)
            if current is None:
                winners[prepared.digest] = CanonicalReference(
                    prepared.source_pk, prepared.source, ordinal
                )
            elif _source_rank(prepared.source) < _source_rank(current.source):
                winners[prepared.digest] = CanonicalReference(
                    prepared.source_pk, prepared.source, ordinal
                )
                stats.canonical_source_promotions += 1

        if not winners:
            skip_summary = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(stats.skipped.items())
            ) or "none"
            raise RuntimeError(
                "No sequences survived validation and source selection. Check "
                f"{', '.join(selected_sources)} against the copied table's source "
                f"values. Current skip counts: {skip_summary}."
            )

        ref_ids = _assign_ref_ids(winners, stats)
        store = connect_write(staged_meta)
        written: set[str] = set()

        with staged_fasta.open("w", encoding="ascii", newline="\n") as fh:
            for row in dbmod.iter_rows(conn, schema, limit=settings.export_limit):
                prepared, _, _ = _prepare_reference(
                    row, schema, settings, allowed_sources
                )
                if prepared is None:
                    continue

                winner = winners[prepared.digest]
                ref_id = ref_ids[prepared.digest]

                # Identical sequences add nothing to a search database — BLAST
                # and HMMER would return the same alignment n times and the
                # E-value would be computed against an inflated database size.
                # The row is kept as provenance instead of being dropped.
                if prepared.source_pk != winner.source_pk:
                    stats.duplicate_sequences += 1
                    stats.skipped["duplicate_sequence"] += 1
                    stats.duplicate_sources[prepared.source] += 1
                    store.execute(
                        "INSERT INTO reference_duplicate "
                        "(ref_id, source_pk, description, ec, source) VALUES (?,?,?,?,?)",
                        (
                            ref_id, prepared.source_pk, prepared.description,
                            prepared.ec, prepared.source,
                        ),
                    )
                    continue

                fh.write(
                    f">{ref_id} EC={prepared.ec or 'NA'} src={prepared.source}\n"
                )
                for i in range(0, len(prepared.sequence), 60):
                    fh.write(prepared.sequence[i:i + 60] + "\n")

                store.execute(
                    "INSERT INTO reference (ref_id, source_pk, description, ec, source, "
                    "length, sequence_sha256, motif, active_site, binding_site, interpretation) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ref_id, prepared.source_pk, prepared.description,
                        prepared.ec, prepared.source, len(prepared.sequence),
                        prepared.digest, _opt(row, schema, "motif"),
                        _opt(row, schema, "active"), _opt(row, schema, "binding"),
                        _opt(row, schema, "interpretation"),
                    ),
                )

                written.add(prepared.digest)
                stats.exported += 1
                stats.sources[prepared.source] += 1
                stats.length_sum += len(prepared.sequence)
                stats.length_min = (
                    len(prepared.sequence) if stats.length_min is None
                    else min(stats.length_min, len(prepared.sequence))
                )
                stats.length_max = (
                    len(prepared.sequence) if stats.length_max is None
                    else max(stats.length_max, len(prepared.sequence))
                )
                if prepared.ec:
                    stats.ec_present += 1
                if prepared.ec_count > 1:
                    stats.multi_ec += 1

                if stats.exported % 50_000 == 0:
                    store.commit()
                    log.info("… %d rows read, %d exported", stats.rows_read, stats.exported)

        if len(written) != len(winners):
            missing = len(winners) - len(written)
            raise RuntimeError(
                f"source table changed during export: {missing} canonical rows disappeared"
            )

        with staged_skipped.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("source_pk\treason\tdetail\n")
            for pk, reason, detail in skipped_rows:
                fh.write(f"{pk}\t{reason}\t{detail}\n")

        store.commit()
        store.close()
        store = None
        staged_fasta.replace(fasta_path)
        staged_meta.replace(meta_path)
        staged_skipped.replace(skipped_path)
    finally:
        if store is not None:
            store.close()
        if conn is not None:
            conn.close()
        staging_ctx.cleanup()

    result = {
        "table": schema.table,
        "database": settings.db_name,
        "primary_key": schema.primary_key,
        "engine": schema.engine,
        "columns_mapped": schema.mapping,
        "columns_missing": schema.missing(),
        "reference_sources": list(selected_sources),
        "fasta_sha256": sha256_file(fasta_path),
        **stats.as_dict(),
    }
    (out_dir / "export_stats.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    log.info("exported %d/%d rows to %s", stats.exported, stats.rows_read, fasta_path.name)
    return result


def _prepare_reference(
    row: dict,
    schema: dbmod.TableSchema,
    settings: Settings,
    allowed_sources: frozenset[str],
) -> tuple[PreparedReference | None, str | None, str]:
    cleaned = clean_sequence(
        row.get(schema.column("sequence")),
        min_length=settings.min_sequence_length,
        max_length=settings.max_reference_length,
        max_ambiguous_fraction=settings.max_ambiguous_fraction,
    )
    if cleaned.sequence is None:
        reason = (cleaned.reason or "invalid").split(":")[0]
        return None, reason, cleaned.reason or ""

    source = normalize_source(row.get(schema.column("source")))
    if source not in allowed_sources:
        return None, "source_not_selected", source

    sequence = cleaned.sequence
    ec_col = schema.column("ec")
    ec, ec_count = normalize_ec(row.get(ec_col) if ec_col else None)
    return (
        PreparedReference(
            source_pk=str(row[schema.primary_key]),
            sequence=sequence,
            digest=hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            ec=ec,
            ec_count=ec_count,
            source=source,
            description=_description(row, schema),
        ),
        None,
        "",
    )


def _source_rank(source: str) -> int:
    return _SOURCE_PRIORITY.get(source, len(_SOURCE_PRIORITY))


def _assign_ref_ids(
    winners: dict[str, CanonicalReference], stats: ExportStats
) -> dict[str, str]:
    ref_ids: dict[str, str] = {}
    used_ids: set[str] = set()
    for digest, winner in sorted(
        winners.items(), key=lambda item: item[1].ordinal
    ):
        ref_id = make_ref_id(winner.source_pk)
        if ref_id in used_ids:
            stats.duplicate_identifiers += 1
            suffix = 2
            while f"{ref_id}_{suffix}" in used_ids:
                suffix += 1
            ref_id = f"{ref_id}_{suffix}"
        used_ids.add(ref_id)
        ref_ids[digest] = ref_id
    return ref_ids


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
