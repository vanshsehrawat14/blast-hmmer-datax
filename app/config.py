"""Single source of configuration for the reference build and the web app.

Everything is read from the environment (or a local `.env`) with the prefix
`ENZYMEX_`, which makes the five database variables named in the EnzymeX
handover notes resolve exactly as documented:

    ENZYMEX_DB_HOST  ENZYMEX_DB_PORT  ENZYMEX_DB_NAME
    ENZYMEX_DB_USER  ENZYMEX_DB_PASSWORD

The password is a SecretStr so it cannot be printed by accident: repr, logs
and the health endpoint all render it as `**********`.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_SOURCE_CHOICES = frozenset({"swissprot", "pdb"})


def _parse_reference_sources(value: str) -> tuple[str, ...]:
    requested = {
        source.strip().lower()
        for source in value.split(",")
        if source.strip()
    }
    if not requested:
        raise ValueError("must select at least one source")
    unsupported = sorted(requested - REFERENCE_SOURCE_CHOICES)
    if unsupported:
        raise ValueError(
            "only swissprot and pdb are supported; "
            f"got {', '.join(unsupported)}"
        )
    return tuple(
        source for source in ("swissprot", "pdb") if source in requested
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENZYMEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------------------------------------------------------- copied database
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "enzymex_copy"
    db_user: str = "enzymex_ro"
    db_password: SecretStr = SecretStr("")
    db_table: str = "enzymesdata"
    db_connect_timeout: int = 10

    # Deliberate friction. The export refuses to run unless the operator has
    # stated in the environment that the target is a copy, so a stray
    # ENZYMEX_DB_HOST pointing at production cannot be used by accident.
    db_confirm_copy: bool = False

    # ---------------------------------------------------------------- filesystem
    reference_dir: Path = REPO_ROOT / "var" / "reference"
    job_dir: Path = REPO_ROOT / "var" / "jobs"

    # ---------------------------------------------------------------- executables
    blastp_bin: str = "blastp"
    makeblastdb_bin: str = "makeblastdb"
    phmmer_bin: str = "phmmer"
    hmmscan_bin: str = "hmmscan"
    hmmbuild_bin: str = "hmmbuild"
    hmmpress_bin: str = "hmmpress"
    mafft_bin: str = "mafft"
    mmseqs_bin: str = "mmseqs"

    # ---------------------------------------------------------------- export filters
    # BLAST, phmmer and profile-HMM construction all consume the same export.
    # Keep this as a comma-separated canonical source list so the selected
    # EnzymeX datasets are explicit and recorded in the build id.
    reference_sources: str = "swissprot,pdb"
    # 30 residues is below any real single-domain protein but above the
    # peptide fragments that pollute TrEMBL; sequences shorter than this
    # produce alignments too short for a meaningful E-value.
    min_sequence_length: int = 30
    max_reference_length: int = 10_000
    # X/B/Z/J carry no residue identity. A reference that is >10% ambiguous
    # inflates alignment length without contributing evidence.
    max_ambiguous_fraction: float = 0.10
    # 0 exports everything. Set it to cap a first build on a large table.
    export_limit: int = 0

    # ---------------------------------------------------------------- submission limits
    max_upload_bytes: int = 1_000_000
    max_query_sequences: int = 10
    max_query_length: int = 5_000

    # ---------------------------------------------------------------- search parameters
    blast_evalue: float = 1e-3
    # NCBI's -max_target_seqs is a search-time cutoff, not a "best N" filter
    # (Shah et al., Bioinformatics 2019), so it is set generously here and the
    # displayed list is truncated afterwards by our own ranking.
    blast_max_target_seqs: int = 500
    phmmer_evalue: float = 1e-3
    hmmscan_evalue: float = 1e-3
    max_hits_per_query: int = 25

    search_threads: int = 2
    blast_timeout_seconds: int = 120
    hmmer_timeout_seconds: int = 300
    # Bounds total work in flight; excess submissions get a 503 rather than
    # queueing until the box runs out of memory.
    max_concurrent_jobs: int = 2

    enable_blast: bool = True
    enable_phmmer: bool = True
    enable_profile_hmm: bool = True

    # ---------------------------------------------------------------- reference build
    build_threads: int = 4
    # 35% identity sits above the alignment twilight zone (Rost 1999) and 80%
    # bidirectional coverage forces members to share a domain architecture,
    # which is what makes a single MSA of the cluster defensible.
    cluster_min_seq_id: float = 0.35
    cluster_coverage: float = 0.80
    # Below 5 members a profile carries little more position-specific signal
    # than phmmer already gives, so it is not worth the build cost.
    profile_min_members: int = 5
    # Purely a runtime bound on MAFFT; hmmbuild down-weights redundancy anyway.
    profile_max_members: int = 500
    profile_min_match_states: int = 40
    build_timeout_seconds: int = 7_200

    # ---------------------------------------------------------------- web
    job_retention_hours: int = 24
    keep_raw_outputs: bool = True
    log_level: str = "INFO"

    @field_validator("reference_sources")
    @classmethod
    def _reference_sources(cls, value: str) -> str:
        return ",".join(_parse_reference_sources(value))

    @field_validator("reference_dir", "job_dir")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def selected_reference_sources(self) -> tuple[str, ...]:
        return _parse_reference_sources(self.reference_sources)

    # -- derived reference artifact paths ------------------------------------
    @property
    def references_fasta(self) -> Path:
        return self.reference_dir / "references.fasta"

    @property
    def metadata_db(self) -> Path:
        return self.reference_dir / "metadata.sqlite3"

    @property
    def blast_db(self) -> Path:
        return self.reference_dir / "blastdb" / "references"

    @property
    def profile_db(self) -> Path:
        return self.reference_dir / "profiles" / "profiles.hmm"

    @property
    def manifest_path(self) -> Path:
        return self.reference_dir / "build_manifest.json"

    def dsn_summary(self) -> str:
        """Connection identity for logs and /health. Never includes the password."""
        return f"{self.db_user}@{self.db_host}:{self.db_port}/{self.db_name}"

    def which(self, binary: str) -> str | None:
        return shutil.which(binary)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
