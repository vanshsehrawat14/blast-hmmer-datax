# Deployment

Two supported paths. Docker Compose is the reproducible one; the micromamba
path is there when Docker is not available on the box.

Both assume a Linux server with network access to the copied EnzymeX MySQL
instance, and neither packages or redistributes EnzymeX data.

## Docker Compose

```bash
git clone https://github.com/vanshsehrawat14/blast-hmmer-datax.git
cd blast-hmmer-datax
cp .env.example .env
$EDITOR .env                       # copied-database credentials; CONFIRM_COPY=true

cd deploy
docker compose build
docker compose run --rm refbuild   # one-off, minutes
docker compose up -d web
curl -s localhost:8000/health | python -m json.tool
```

The image is built from `environment.yml`, so BLAST+, HMMER, MAFFT and
MMseqs2 are the same pinned versions the manifest will record. Artifacts live
on a named volume mounted at `/srv/data`, never in the image.

`refbuild` is a one-off container, not a service. It reads the whole copied
table and forks MAFFT once per family; it must never be reachable from a
request.

Rebuild references after the copy changes:

```bash
docker compose run --rm refbuild
docker compose restart web
```

Compose caps the web container at 4 CPU / 4 GB. Raise both if the copied
`enzymesdata` is large — BLAST and HMMER memory scales with the reference
database.

## micromamba + systemd

```bash
sudo useradd -r -m -d /opt/blast-hmmer-datax -s /usr/sbin/nologin enzymex
sudo -u enzymex git clone https://github.com/vanshsehrawat14/blast-hmmer-datax.git \
     /opt/blast-hmmer-datax
cd /opt/blast-hmmer-datax

sudo -u enzymex bash scripts/00_setup.sh        # micromamba + environment.yml
sudo -u enzymex ~enzymex/.local/bin/micromamba run -n blast-hmmer-datax \
     pip install --no-deps -e .
```

Credentials go in a root-owned file the service user can read, not in the
repository:

```bash
sudo install -m 0640 -o root -g enzymex /dev/null /etc/enzymex-blast-hmmer.env
sudo $EDITOR /etc/enzymex-blast-hmmer.env       # same keys as .env.example
```

Build and start:

```bash
sudo -u enzymex env $(cat /etc/enzymex-blast-hmmer.env | xargs) \
     ~enzymex/.local/bin/micromamba run -n blast-hmmer-datax \
     python -m app.references.cli all

sudo cp deploy/enzymex-blast-hmmer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now enzymex-blast-hmmer
curl -s localhost:8000/health | python -m json.tool
```

The unit runs with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
`PrivateTmp` and a `MemoryMax`, with `var/` as the only writable path. The
application feeds untrusted input to external binaries, so it gets as little
of the system as it can still work with.

## Development

```bash
bash scripts/00_setup.sh
cp .env.example .env && $EDITOR .env
make refbuild
make serve            # http://127.0.0.1:8000 with reload
```

## Behind a reverse proxy

Bind to loopback (both paths above do) and terminate TLS in nginx or Caddy.
Two things matter:

* set the body size limit to match `ENZYMEX_MAX_UPLOAD_BYTES`
  (`client_max_body_size 1m;` for the default);
* set the proxy read timeout above `ENZYMEX_HMMER_TIMEOUT_SECONDS`, or a slow
  search will be cut off by the proxy and reported as a gateway error instead
  of the application's own timeout message.

This is a test server. If it is reachable beyond the machine, put
authentication in front of it — there is none built in.

## Health

`GET /health` returns JSON and never contains a credential:

```json
{
  "status": "ok",
  "reference_build_id": "261967e8d173",
  "built_at": "2026-07-26T05:09:47+00:00",
  "reference_sequences": 2380,
  "profiles": 64,
  "artifacts": {"references_fasta": true, "metadata_db": true,
                "blast_db": true, "profile_db": true},
  "methods": {"blastp": {"enabled": true, "ok": true},
              "phmmer": {"enabled": true, "ok": true},
              "hmmscan": {"enabled": true, "ok": true}},
  "copied_database": {"target": "enzymex_ro@10.0.0.5:3306/enzymex_copy",
                      "reachable": true, "detail": "MySQL 8.4.10 at ..."},
  "tool_versions": {"blastp": "blastp: 2.16.0+", "phmmer": "HMMER 3.4 ...", ...}
}
```

| status | HTTP | meaning |
|---|---|---|
| `ok` | 200 | artifacts present, at least one method usable, copy reachable |
| `degraded` | 200 | as above, but the copied database is unreachable — searches still work, rebuilds will not |
| `unavailable` | 503 | no usable artifacts; run the reference build |

The database probe is deliberately not fatal: a user request never touches
MySQL, so an unreachable copy is a rebuild problem, not an outage.

## Housekeeping

```bash
make status          # what is built
make clean-jobs      # delete stored jobs now
make clean           # delete every generated artifact under var/
```

Job directories are swept automatically on each new submission, using
`ENZYMEX_JOB_RETENTION_HOURS` (default 24). No cron job is needed; an idle
server does no work.
