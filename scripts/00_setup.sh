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
"$MM" run -n blast-hmmer-datax hmmsearch -h | sed -n 2p
"$MM" run -n blast-hmmer-datax mafft --version 2>&1 | head -1
"$MM" run -n blast-hmmer-datax python --version

echo
echo "[setup] done. Run the full experiment with:"
echo "  make all"
