# Single image for both the reference build and the web application.
#
# micromamba is the base because BLAST+, HMMER, MAFFT and MMseqs2 are not
# pip-installable and the distro packages lag; environment.yml pins the exact
# versions the manifest will record, so a rebuild on another machine produces
# the same tool versions.
FROM mambaorg/micromamba:2.0-ubuntu24.04

# Placed early so a code change does not re-solve the environment.
COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml \
    && micromamba clean --all --yes

ARG MAMBA_DOCKERFILE_ACTIVATE=1
WORKDIR /srv/app

COPY --chown=$MAMBA_USER:$MAMBA_USER pyproject.toml ./
COPY --chown=$MAMBA_USER:$MAMBA_USER app ./app
COPY --chown=$MAMBA_USER:$MAMBA_USER scripts ./scripts
COPY --chown=$MAMBA_USER:$MAMBA_USER tests ./tests
COPY --chown=$MAMBA_USER:$MAMBA_USER data ./data
COPY --chown=$MAMBA_USER:$MAMBA_USER Makefile ./

# Installed without dependencies: environment.yml already pinned them, and
# letting pip resolve again here would silently drift from those pins.
RUN pip install --no-deps -e .

# Artifacts live on a mounted volume, never in the image. The copied EnzymeX
# database is not packaged or redistributed.
#
# The mount point is created and chowned here rather than at runtime: Docker
# seeds a fresh named volume from the image's directory, so without this the
# volume comes up root-owned and the unprivileged service user cannot write
# the reference build into it.
USER root
RUN mkdir -p /srv/data && chown -R $MAMBA_USER:$MAMBA_USER /srv/data
USER $MAMBA_USER

ENV ENZYMEX_REFERENCE_DIR=/srv/data/reference \
    ENZYMEX_JOB_DIR=/srv/data/jobs \
    PYTHONUNBUFFERED=1
VOLUME ["/srv/data"]

EXPOSE 8000

# Absolute interpreter path: HEALTHCHECK does not go through the image's
# ENTRYPOINT, so the conda environment is not activated for it and a bare
# `python` is not on PATH. /health returns 503 until the reference build has
# run, which is the behaviour we want reported.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["/opt/conda/bin/python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status == 200 else 1)"]

CMD ["uvicorn", "app.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
