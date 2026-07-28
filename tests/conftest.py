"""Shared fixtures.

Tests never read the developer's `.env`: every Settings object is built with
`_env_file=None` and explicit values, so a test run cannot accidentally point
at a real database or a real reference build.

`_env_file=None` does not cover the *process* environment, which
pydantic-settings always reads. `_isolate_environment` below closes that gap,
because the documented way to run the `mysql` tests is to export `ENZYMEX_DB_*`
into the shell, and those variables would otherwise reach every other test too.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent

# A real Swiss-Prot sequence (P00330, yeast alcohol dehydrogenase 1) used
# wherever a test needs something that is unambiguously a protein.
ADH1_YEAST = (
    "MSIPETQKGVIFYESHGKLEYKDIPVPKPKANELLINVKYSGVCHTDLHAWHGDWPLPVKLPLVGGHEGAGVVVGMGENVKG"
    "WKIGDYAGIKWLNGSCMACEYCELGNESNCPHADLSGYTHDGSFQQYATADAVQAAHIPQGTDLAQVAPILCAGITVYKALK"
    "SANLMAGHWVAISGAAGGLGSLAVQYAKAMGYRVLGIDGGEGKEELFRSIGGEVFIDFTKEKDIVGAVLKATDGGAHGVINV"
    "SVSEAAIEASTRYVRANGTTVLVGMPAGAKCCSDVFNQVVKSISIVGSYVGNRADTREALDFFARGLVKSPIKVVGLSTLPE"
    "IYEKMEKGQIVGRYVVDTSK"
)


@pytest.fixture(autouse=True)
def _isolate_environment(request, monkeypatch):
    """Strip ambient `ENZYMEX_*` variables from every non-`mysql` test.

    Without this, running the suite in a shell configured for the copied
    database silently changes what the unit tests measure: thresholds, feature
    flags and artifact paths all come from the same prefix.
    """
    if request.node.get_closest_marker("mysql"):
        return
    for key in [k for k in os.environ if k.startswith("ENZYMEX_")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        db_host="127.0.0.1",
        db_name="test_db",
        db_user="test_user",
        db_password="unused",
        reference_dir=tmp_path / "reference",
        job_dir=tmp_path / "jobs",
        max_query_sequences=5,
        max_query_length=2000,
        max_upload_bytes=50_000,
        job_retention_hours=1,
    )


@pytest.fixture
def live_settings() -> Settings:
    """Settings for the developer's real build. Skips when nothing is built."""
    s = Settings()
    if not s.references_fasta.exists() or not s.metadata_db.exists():
        pytest.skip("no reference build present; run `make refbuild` first")
    return s


def have_tools(*binaries: str) -> bool:
    return all(shutil.which(b) for b in binaries)


needs_tools = pytest.mark.skipif(
    not have_tools("blastp", "phmmer", "hmmscan", "makeblastdb"),
    reason="BLAST+/HMMER not on PATH",
)

needs_mysql = pytest.mark.skipif(
    not os.environ.get("ENZYMEX_DB_PASSWORD"),
    reason="no copied-database credentials in the environment",
)
