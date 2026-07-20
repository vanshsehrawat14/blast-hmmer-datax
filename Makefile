ENV_NAME := blast-hmmer-datax

# Every recipe runs inside the conda/mamba environment defined in
# environment.yml, so BLAST+, HMMER and MAFFT resolve without activating
# the environment in your shell first.
# scripts/00_setup.sh installs micromamba to ~/.local/bin, which is not on
# PATH in a non-login shell, so check that location explicitly as well.
CONDA_EXE ?= $(shell command -v micromamba \
	|| command -v mamba \
	|| command -v conda \
	|| ([ -x "$$HOME/.local/bin/micromamba" ] && echo "$$HOME/.local/bin/micromamba"))

ifeq ($(strip $(CONDA_EXE)),)
$(error No micromamba/mamba/conda found. Run `bash scripts/00_setup.sh` first, \
or pass one explicitly: `make all CONDA_EXE=/path/to/micromamba`)
endif

export MAMBA_ROOT_PREFIX ?= $(HOME)/micromamba
RUN := $(CONDA_EXE) run -n $(ENV_NAME)

.PHONY: all env data blast hmmer diamond parse test clean clean-all versions help

help:
	@echo "make env       - create the conda environment from environment.yml"
	@echo "make data      - download the EC 1.1.1.1 sequences from UniProt"
	@echo "make blast     - build the BLAST database and run blastp"
	@echo "make hmmer     - run phmmer, and build + search the profile HMM"
	@echo "make diamond   - optional: run DIAMOND over the same sequences"
	@echo "make parse     - normalize all results into CSV/JSON + report"
	@echo "make test      - verify the pipeline outputs"
	@echo "make all       - data + blast + hmmer + diamond + parse + test"
	@echo "make versions  - print installed tool versions"
	@echo "make clean     - remove generated results and processed data"

env:
	$(CONDA_EXE) create -y -f environment.yml

data:
	$(RUN) python scripts/01_download_uniprot.py

blast:
	$(RUN) bash scripts/02_run_blast.sh

hmmer:
	$(RUN) bash scripts/03_run_hmmer.sh

diamond:
	$(RUN) bash scripts/05_run_diamond.sh

parse:
	$(RUN) python scripts/04_parse_results.py

test:
	$(RUN) python tests/test_pipeline.py

all: data blast hmmer diamond parse test
	@echo
	@echo "Done. See results/comparison/comparison_report.md"

versions:
	@$(RUN) blastp -version | head -1
	@$(RUN) makeblastdb -version | head -1
	@$(RUN) hmmsearch -h | sed -n 2p
	@$(RUN) phmmer -h | sed -n 2p
	@$(RUN) mafft --version 2>&1 | head -1
	@$(RUN) python --version
	@$(RUN) diamond --version 2>/dev/null || echo "diamond: not installed (optional)"

clean:
	rm -rf results/blast/* results/hmmer/* results/diamond/* results/comparison/* data/processed/*

clean-all: clean
	rm -rf data/raw/*.fasta data/raw/metadata.tsv data/raw/provenance.json
