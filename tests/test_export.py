"""Reference export from enzymesdata.

The MySQL layer is stubbed so the export's own logic — validation, dedup,
identifier assignment, metadata mapping, skip reporting — is tested without a
database. `tests/test_database.py` covers the real driver path when
credentials are available.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.references import db as dbmod
from app.references import export as exportmod
from app.references.export import (
    export_references, make_ref_id, normalize_ec, normalize_source, sha256_file,
)
from tests.conftest import ADH1_YEAST

SCHEMA = dbmod.TableSchema(
    table="enzymesdata",
    columns=["id", "accession", "description", "sequence", "ec", "motif",
             "active", "binding", "interpretation", "source", "modified", "created"],
    mapping={
        "sequence": "sequence", "description": "description", "ec": "ec",
        "source": "source", "motif": "motif", "active": "active",
        "binding": "binding", "interpretation": "interpretation",
        "accession": "accession",
    },
    primary_key="id",
    row_count=0,
    engine="InnoDB",
)


def row(id_, seq, **kw):
    base = {"id": id_, "sequence": seq, "accession": f"ACC{id_}",
            "description": "some enzyme", "ec": "1.1.1.1", "source": "Swiss-Prot",
            "motif": None, "active": None, "binding": None, "interpretation": None}
    base.update(kw)
    return base


@pytest.fixture
def fake_db(monkeypatch):
    """Install a row source; returns a setter the test calls with its rows."""
    holder: dict = {"rows": []}
    monkeypatch.setattr(dbmod, "connect", lambda s: _FakeConn())
    monkeypatch.setattr(exportmod.dbmod, "connect", lambda s: _FakeConn())
    monkeypatch.setattr(exportmod.dbmod, "inspect_schema", lambda c, t: SCHEMA)
    monkeypatch.setattr(exportmod.dbmod, "begin_consistent_snapshot", lambda c: None)
    monkeypatch.setattr(exportmod.dbmod, "iter_rows",
                        lambda c, s, limit=0: iter(
                            holder["rows"][:limit] if limit > 0 else holder["rows"]
                        ))
    return holder


class _FakeConn:
    def close(self):
        pass


# ---------------------------------------------------------------- pure helpers
@pytest.mark.parametrize("raw,expected", [
    ("1.1.1.1", "1.1.1.1"),
    ("1.1.1.1; 1.1.1.71", "1.1.1.1;1.1.1.71"),
    ("1.1.1.71,1.1.1.1", "1.1.1.1;1.1.1.71"),      # sorted, so order is stable
    ("1.1.1.1 1.1.1.1", "1.1.1.1"),                # deduplicated
    ("1.1.-.-", "1.1.-.-"),                        # partial EC is valid
    ("1.1.1.n5", "1.1.1.n5"),                      # preliminary EC is valid
    ("not an ec", None),
    ("", None),
    (None, None),
])
def test_ec_normalization(raw, expected):
    assert normalize_ec(raw)[0] == expected


def test_ec_token_count_flags_multifunctional_entries():
    assert normalize_ec("1.1.1.1; 4.2.1.1")[1] == 2
    assert normalize_ec("1.1.1.1")[1] == 1


@pytest.mark.parametrize("raw,expected", [
    ("Swiss-Prot", "swissprot"), ("sp", "swissprot"), ("UniProtKB/TrEMBL", "trembl"),
    ("PDB", "pdb"), ("KEGG", "kegg"), ("", "unknown"), (None, "unknown"),
])
def test_source_normalization(raw, expected):
    assert normalize_source(raw) == expected


def test_documented_uniprotid_column_is_an_accession_alias():
    assert "uniprotid" in dbmod.COLUMN_ALIASES["accession"]


def test_consistent_snapshot_is_repeatable_read_and_read_only():
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            statements.append(statement)

    class Connection:
        def cursor(self, _cursor_type):
            return Cursor()

    dbmod.begin_consistent_snapshot(Connection())
    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
    ]


def test_reference_ids_are_stable_and_fasta_safe():
    assert make_ref_id(42) == "EXR42"
    # Anything that would split a defline or confuse BLAST's id parser is
    # flattened, because the id has to survive as a single token.
    assert make_ref_id("a b|c") == "EXR" + "a_b_c"
    assert " " not in make_ref_id("x y") and "|" not in make_ref_id("a|b")


# ---------------------------------------------------------------- export
def test_clean_rows_export_with_metadata(settings, fake_db):
    fake_db["rows"] = [row(1, ADH1_YEAST), row(2, ADH1_YEAST[:200], ec="4.2.1.1",
                                               source="PDB"),
                       row(3, ADH1_YEAST[:201], source="KEGG")]
    stats = export_references(settings)
    assert stats["exported"] == 2
    assert stats["reference_sources"] == ["swissprot", "pdb"]
    assert stats["sources"] == {"swissprot": 1, "pdb": 1}
    assert stats["selected_rows_by_source"] == {"swissprot": 1, "pdb": 1}
    assert stats["excluded_rows_by_source"] == {"kegg": 1}
    assert stats["skipped_by_reason"] == {"source_not_selected": 1}
    assert stats["rows_read"] == stats["exported"] + stats["skipped_total"]

    fasta = settings.references_fasta.read_text()
    assert fasta.startswith(">EXR1 EC=1.1.1.1 src=swissprot\n")
    assert ">EXR2 EC=4.2.1.1 src=pdb" in fasta
    assert "EXR3" not in fasta

    conn = sqlite3.connect(settings.metadata_db)
    conn.row_factory = sqlite3.Row
    rows = {r["ref_id"]: dict(r) for r in conn.execute("SELECT * FROM reference")}
    assert rows["EXR1"]["ec"] == "1.1.1.1"
    assert rows["EXR1"]["source"] == "swissprot"
    assert rows["EXR1"]["source_pk"] == "1"
    assert rows["EXR1"]["description"] == "ACC1 some enzyme"
    assert rows["EXR1"]["length"] == len(ADH1_YEAST)


def test_every_bad_row_shape_is_skipped_with_a_reason(settings, fake_db):
    dna = "ATGGCGTAGCTAGCTAGCATCGATCGATCGATCGTAGCTAGCTAGCTAGCATCGATCGAT" * 2
    fake_db["rows"] = [
        row(1, ADH1_YEAST),                                  # keeper
        row(2, None),                                        # null
        row(3, "   "),                                       # empty
        row(4, "MKV"),                                       # too short
        row(5, ADH1_YEAST[:80] + "*" + ADH1_YEAST[80:160]),  # internal stop
        row(6, "X" * 300),                                   # all ambiguous
        row(7, dna),                                         # nucleotide text
        row(8, ADH1_YEAST[:60] + "###"),                     # invalid characters
    ]
    stats = export_references(settings)
    assert stats["exported"] == 1
    assert stats["skipped_by_reason"] == {
        "null_sequence": 1, "empty_sequence": 1, "too_short": 1,
        "internal_stop": 1, "excessive_ambiguity": 1,
        "looks_like_nucleotide": 1, "invalid_characters": 1,
    }

    skipped = (settings.reference_dir / "skipped.tsv").read_text().splitlines()
    assert skipped[0] == "source_pk\treason\tdetail"
    assert len(skipped) == 8
    assert any(line.startswith("7\tlooks_like_nucleotide") for line in skipped)


def test_identical_sequences_are_merged_but_provenance_is_kept(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source="Swiss-Prot"),
        row(2, ADH1_YEAST, source="PDB", accession="ACC2"),
        row(3, ADH1_YEAST, source="KEGG", accession="ACC3"),
    ]
    stats = export_references(settings)
    assert stats["exported"] == 1
    assert stats["duplicate_sequences_merged"] == 1
    assert stats["skipped_by_reason"] == {
        "duplicate_sequence": 1,
        "source_not_selected": 1,
    }
    assert stats["duplicate_rows_by_source"] == {"pdb": 1}

    conn = sqlite3.connect(settings.metadata_db)
    dupes = conn.execute(
        "SELECT ref_id, source_pk, source FROM reference_duplicate ORDER BY source_pk"
    ).fetchall()
    assert dupes == [("EXR1", "2", "pdb")]


def test_swissprot_metadata_wins_when_pdb_row_comes_first(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source="PDB", accession="1ABC_1",
            description="PDB construct", ec="9.9.9.9"),
        row(2, ADH1_YEAST, source="Swiss-Prot", accession="P00330",
            description="reviewed enzyme", ec="1.1.1.1"),
    ]

    stats = export_references(settings)

    assert stats["exported"] == 1
    assert stats["canonical_source_promotions"] == 1
    assert stats["sources"] == {"swissprot": 1}
    assert stats["duplicate_rows_by_source"] == {"pdb": 1}
    assert settings.references_fasta.read_text().startswith(
        ">EXR2 EC=1.1.1.1 src=swissprot\n"
    )

    conn = sqlite3.connect(settings.metadata_db)
    canonical = conn.execute(
        "SELECT ref_id, source_pk, description, ec, source FROM reference"
    ).fetchone()
    assert canonical == (
        "EXR2", "2", "P00330 reviewed enzyme", "1.1.1.1", "swissprot",
    )
    duplicate = conn.execute(
        "SELECT ref_id, source_pk, description, ec, source FROM reference_duplicate"
    ).fetchone()
    assert duplicate == (
        "EXR2", "1", "1ABC_1 PDB construct", "9.9.9.9", "pdb",
    )


def test_canonical_metadata_is_not_merged_across_sources(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source="PDB", ec="1.1.1.1"),
        row(2, ADH1_YEAST, source="Swiss-Prot", ec=None),
    ]

    export_references(settings)

    conn = sqlite3.connect(settings.metadata_db)
    assert conn.execute(
        "SELECT source_pk, ec, source FROM reference"
    ).fetchone() == ("2", None, "swissprot")
    assert conn.execute(
        "SELECT source_pk, ec, source FROM reference_duplicate"
    ).fetchone() == ("1", "1.1.1.1", "pdb")


def test_disallowed_duplicate_cannot_hide_an_allowed_reference(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source="TrEMBL"),
        row(2, ADH1_YEAST, source="RCSB", accession="1ABC_1"),
    ]

    stats = export_references(settings)

    assert stats["exported"] == 1
    assert stats["duplicate_sequences_merged"] == 0
    assert stats["excluded_rows_by_source"] == {"trembl": 1}
    assert settings.references_fasta.read_text().startswith(
        ">EXR2 EC=1.1.1.1 src=pdb\n"
    )


@pytest.mark.parametrize("source", ["Swiss-Prot", "PDB"])
def test_same_source_duplicate_keeps_first_row(settings, fake_db, source):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source=source, description="first"),
        row(2, ADH1_YEAST, source=source, description="second"),
    ]

    stats = export_references(settings)

    assert stats["canonical_source_promotions"] == 0
    assert settings.references_fasta.read_text().startswith(">EXR1 ")
    conn = sqlite3.connect(settings.metadata_db)
    assert conn.execute(
        "SELECT source_pk, description FROM reference"
    ).fetchone() == ("1", "ACC1 first")
    assert conn.execute(
        "SELECT source_pk, description FROM reference_duplicate"
    ).fetchone() == ("2", "ACC2 second")


def test_sequence_qc_precedes_source_selection(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST),
        row(2, None, source="KEGG"),
    ]

    stats = export_references(settings)

    assert stats["skipped_by_reason"] == {"null_sequence": 1}
    assert stats["excluded_rows_by_source"] == {}


def test_whitespace_and_residue_numbering_are_normalized(settings, fake_db):
    messy = "  1 " + ADH1_YEAST[:60] + "\n 61 " + ADH1_YEAST[60:180] + "  "
    fake_db["rows"] = [row(1, messy)]
    export_references(settings)
    body = "".join(l for l in settings.references_fasta.read_text().splitlines()
                   if not l.startswith(">"))
    assert body == ADH1_YEAST[:180]


def test_missing_optional_columns_become_null_not_an_error(settings, monkeypatch, fake_db):
    sparse = dbmod.TableSchema(
        table="enzymesdata", columns=["id", "sequence", "source"],
        mapping={"sequence": "sequence", "source": "source"},
        primary_key="id", row_count=1)
    monkeypatch.setattr(exportmod.dbmod, "inspect_schema", lambda c, t: sparse)
    fake_db["rows"] = [{"id": 1, "sequence": ADH1_YEAST, "source": "Swiss-Prot"}]

    stats = export_references(settings)
    assert stats["exported"] == 1
    assert "ec" in stats["columns_missing"] and "description" in stats["columns_missing"]
    assert "source" not in stats["columns_missing"]
    assert ">EXR1 EC=NA src=swissprot" in settings.references_fasta.read_text()


def test_missing_source_column_fails_closed(settings, monkeypatch, fake_db):
    sparse = dbmod.TableSchema(
        table="enzymesdata", columns=["id", "sequence"],
        mapping={"sequence": "sequence"}, primary_key="id", row_count=1)
    monkeypatch.setattr(exportmod.dbmod, "inspect_schema", lambda c, t: sparse)
    fake_db["rows"] = [{"id": 1, "sequence": ADH1_YEAST}]
    settings.reference_dir.mkdir(parents=True)
    settings.references_fasta.write_text("existing fasta")
    settings.metadata_db.write_text("existing metadata")

    with pytest.raises(ValueError, match="source column"):
        export_references(settings)
    assert settings.references_fasta.read_text() == "existing fasta"
    assert settings.metadata_db.read_text() == "existing metadata"


def test_export_is_byte_identical_on_a_second_run(settings, fake_db):
    fake_db["rows"] = [row(i, ADH1_YEAST[: 100 + i]) for i in range(1, 20)]
    export_references(settings)
    first = sha256_file(settings.references_fasta)
    fake_db["rows"] = [row(i, ADH1_YEAST[: 100 + i]) for i in range(1, 20)]
    export_references(settings)
    assert sha256_file(settings.references_fasta) == first


def test_export_limit_applies_to_the_same_rows_in_both_passes(settings, fake_db):
    fake_db["rows"] = [
        row(1, ADH1_YEAST, source="PDB"),
        row(2, ADH1_YEAST, source="Swiss-Prot"),
        row(3, ADH1_YEAST[:200], source="Swiss-Prot"),
    ]
    settings.export_limit = 2

    stats = export_references(settings)

    assert stats["rows_read"] == 2
    assert stats["exported"] == 1
    assert settings.references_fasta.read_text().startswith(
        ">EXR2 EC=1.1.1.1 src=swissprot\n"
    )
    assert "EXR3" not in settings.references_fasta.read_text()


def test_export_with_nothing_usable_fails_loudly(settings, fake_db):
    fake_db["rows"] = [row(1, None), row(2, "MKV")]
    with pytest.raises(
        RuntimeError,
        match=r"Current skip counts: null_sequence=1, too_short=1",
    ):
        export_references(settings)


def test_non_transactional_table_is_rejected(settings, monkeypatch, fake_db):
    myisam = dbmod.TableSchema(
        table=SCHEMA.table,
        columns=SCHEMA.columns,
        mapping=SCHEMA.mapping,
        primary_key=SCHEMA.primary_key,
        row_count=1,
        engine="MyISAM",
    )
    monkeypatch.setattr(exportmod.dbmod, "inspect_schema", lambda c, t: myisam)
    fake_db["rows"] = [row(1, ADH1_YEAST)]

    with pytest.raises(ValueError, match="non-transactional engine MyISAM"):
        export_references(settings)


def test_second_pass_failure_preserves_existing_artifacts(
    settings, monkeypatch, fake_db
):
    rows = [row(1, ADH1_YEAST), row(2, ADH1_YEAST[:200], source="PDB")]
    calls = 0

    def two_passes(conn, schema, limit=0):
        nonlocal calls
        calls += 1
        if calls == 1:
            return iter(rows)

        def fail_mid_scan():
            yield rows[0]
            raise RuntimeError("second pass failed")

        return fail_mid_scan()

    monkeypatch.setattr(exportmod.dbmod, "iter_rows", two_passes)
    settings.reference_dir.mkdir(parents=True)
    settings.references_fasta.write_bytes(b"old fasta")
    settings.metadata_db.write_bytes(b"old metadata")

    with pytest.raises(RuntimeError, match="second pass failed"):
        export_references(settings)

    assert settings.references_fasta.read_bytes() == b"old fasta"
    assert settings.metadata_db.read_bytes() == b"old metadata"
    assert not list(settings.reference_dir.glob(".export-*"))


def test_build_id_changes_with_data_and_with_filters(settings, fake_db):
    from app.references.manifest import compute_build_id
    a = compute_build_id(settings, "sha-one")
    assert compute_build_id(settings, "sha-one") == a          # reproducible
    assert compute_build_id(settings, "sha-two") != a          # data changed
    settings.reference_sources = "pdb,swissprot"
    assert compute_build_id(settings, "sha-one") == a          # same source policy
    settings.reference_sources = "swissprot,pdb"
    settings.min_sequence_length = 50
    assert compute_build_id(settings, "sha-one") != a          # filter changed
    settings.reference_sources = "swissprot"
    assert compute_build_id(settings, "sha-one") != a          # source policy changed


def test_reference_source_setting_is_canonical_and_restricted():
    from app.config import Settings

    configured = Settings(
        _env_file=None, reference_sources="pdb, swissprot, pdb"
    )
    assert configured.reference_sources == "swissprot,pdb"
    assert configured.selected_reference_sources == ("swissprot", "pdb")

    with pytest.raises(ValueError, match="only swissprot and pdb"):
        Settings(_env_file=None, reference_sources="swissprot,kegg")
