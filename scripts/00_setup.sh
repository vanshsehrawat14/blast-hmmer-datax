#!/usr/bin/env bash
# Install micromamba (if needed) and create the project environment.
#
# Works on Linux and on Windows inside WSL2. Requires no root access, which is
# why micromamba is preferred here over `apt install ncbi-blast+ hmmer`.
set -euo pipefail

cd "$(dirname "$0")/.."

MM_BIN="${MM_BIN:-$HOME/.local/bin/micromamba}"
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"

if command -v micromamba >/dev/null 2>&1; then
  MM=$(command -v micromamba)
elif [ -x "$MM_BIN" ]; then
  MM="$MM_BIN"
else
  echo "[setup] installing micromamba to $MM_BIN"
  mkdir -p "$(dirname "$MM_BIN")"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /tmp bin/micromamba
  mv /tmp/bin/micromamba "$MM_BIN"
  chmod +x "$MM_BIN"
  MM="$MM_BIN"
fi

echo "[setup] micromamba $("$MM" --version)"
echo "[setup] creating environment from environment.yml"
"$MM" create -y -f environment.yml

echo
echo "[setup] installed versions:"
"$MM" run -n blast-hmmer-datax blastp -version | head -1
"$MM" run -n blast-hmmer-datax hmmscan -h | sed -n 2p
"$MM" run -n blast-hmmer-datax mafft --version 2>&1 | head -1
"$MM" run -n blast-hmmer-datax mmseqs version 2>/dev/null | tail -1 | sed 's/^/mmseqs /'
"$MM" run -n blast-hmmer-datax python --version

# The application package itself, so `enzymex-refbuild` and `app.*` resolve.
echo "[setup] installing the application (editable, no dependency resolution)"
"$MM" run -n blast-hmmer-datax pip install --no-deps -e . >/dev/null

echo
echo "[setup] done. Next:"
echo "  cp .env.example .env    # copied-database credentials"
echo "  make refbuild           # build the test server's reference artifacts"
echo "  make serve              # http://127.0.0.1:8000"
echo
echo "  make poc-all            # or: the original proof-of-concept pipeline"
