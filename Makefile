ENV_NAME := blast-hmmer-datax

# Every recipe runs inside the conda/mamba environment defined in
# environment.yml, so BLAST+, HMMER, MAFFT and MMseqs2 resolve without
# activating the environment in your shell first.
# scripts/00_setup.sh installs micromamba to ~/.local/bin, which is not on
# PATH in a non-login shell, so check that location explicitly as well.
CONDA_EXE ?= $(shell command -v micromamba \
	|| command -v mamba \
	|| command -v conda \
	|| ([ -x "$$HOME/.local/bin/micromamba" ] && echo "$$HOME/.local/bin/micromamba"))

ifeq ($(strip $(CONDA_EXE)),)
$(error No micromamba/mamba/conda found. Run `bash scripts/00_setup.sh` first, \
or pass one explicitly: `make test CONDA_EXE=/path/to/micromamba`)
endif

export MAMBA_ROOT_PREFIX ?= $(HOME)/micromamba
RUN := $(CONDA_EXE) run -n $(ENV_NAME)
PORT ?= 8000
HOST ?= 127.0.0.1

.PHONY: help env inspect export blast hmmer refbuild status serve serve-prod \
        health test test-unit test-tools test-db clean clean-jobs versions \
        poc-all poc-data poc-blast poc-hmmer poc-diamond poc-parse poc-test poc-clean

help:
	@echo "Test server"
	@echo "  make env         - create the conda environment from environment.yml"
	@echo "  make inspect     - read-only report on the copied enzymesdata table"
	@echo "  make refbuild    - export + BLAST db + profile HMMs + manifest"
	@echo "  make export      - export references only"
	@echo "  make blast       - build the BLAST database only"
	@echo "  make hmmer       - cluster and build the profile HMMs only"
	@echo "  make status      - what is currently built"
	@echo "  make serve       - development server on $(HOST):$(PORT) (reload)"
	@echo "  make serve-prod  - production-style server (no reload, 2 workers)"
	@echo "  make health      - curl the health endpoint"
	@echo "  make test        - full test suite"
	@echo "  make test-unit   - tests that need no external tools or database"
	@echo "  make clean-jobs  - delete stored job directories"
	@echo "  make clean       - delete every generated artifact under var/"
	@echo "  make versions    - print installed tool versions"
	@echo
	@echo "Proof of concept (original EC 1.1.1.1 comparison)"
	@echo "  make poc-all     - download, run blast/hmmer/diamond, parse, verify"

env:
	$(CONDA_EXE) create -y -f environment.yml

# ---------------------------------------------------------------- reference build
inspect:
	$(RUN) python -m app.references.cli inspect

export:
	$(RUN) python -m app.references.cli export

blast:
	$(RUN) python -m app.references.cli blast

hmmer:
	$(RUN) python -m app.references.cli hmmer

refbuild:
	$(RUN) python -m app.references.cli all

status:
	$(RUN) python -m app.references.cli status

# ---------------------------------------------------------------- web
serve:
	$(RUN) uvicorn app.web.app:app --host $(HOST) --port $(PORT) --reload

serve-prod:
	$(RUN) uvicorn app.web.app:app --host 0.0.0.0 --port $(PORT) --workers 2

health:
	@curl -sS http://$(HOST):$(PORT)/health | $(RUN) python -m json.tool

# ---------------------------------------------------------------- tests
test:
	$(RUN) python -m pytest -q

test-unit:
	$(RUN) python -m pytest -q -m "not tools and not mysql"

test-tools:
	$(RUN) python -m pytest -q -m tools

# Needs ENZYMEX_DB_* in the environment or .env, pointing at a COPY.
test-db:
	$(RUN) python -m pytest -q -m mysql

# ---------------------------------------------------------------- housekeeping
versions:
	@$(RUN) blastp -version | head -1
	@$(RUN) makeblastdb -version | head -1
	@$(RUN) hmmscan -h | sed -n 2p
	@$(RUN) phmmer -h | sed -n 2p
	@$(RUN) mafft --version 2>&1 | head -1
	@$(RUN) mmseqs version 2>/dev/null | tail -1 | sed 's/^/mmseqs /'
	@$(RUN) python --version
	@$(RUN) diamond --version 2>/dev/null || echo "diamond: not installed (optional)"

clean-jobs:
	rm -rf var/jobs/*

clean:
	rm -rf var/reference var/jobs var/build_all.log

# ---------------------------------------------------------------- proof of concept
# The original EC 1.1.1.1 comparison, kept because its curated 27-sequence
# dataset is the offline fixture the end-to-end tests build on.
poc-data:
	$(RUN) python scripts/01_download_uniprot.py

poc-blast:
	$(RUN) bash scripts/02_run_blast.sh

poc-hmmer:
	$(RUN) bash scripts/03_run_hmmer.sh

poc-diamond:
	$(RUN) bash scripts/05_run_diamond.sh

poc-parse:
	$(RUN) python scripts/04_parse_results.py

poc-test:
	$(RUN) python tests/test_pipeline.py

poc-all: poc-data poc-blast poc-hmmer poc-diamond poc-parse poc-test
	@echo
	@echo "Done. See results/comparison/comparison_report.md"

poc-clean:
	rm -rf results/blast/* results/hmmer/* results/diamond/* results/comparison/* \
	       data/processed/*
