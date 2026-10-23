# shellcheck shell=bash
# The public site's steps, shared by deploy/deploy_pi.sh and deploy/publish.sh
# (sourced, as root). The site serves a read-only copy of the published
# database from PUBLIC_SITE; the copy it replaced is kept as
# greek.db.previous. deploy/README.md, "The public site".

PUBLIC_USER="greekpublic"
PUBLIC_ROOT="/opt/ancient-greek-reader/public"
PUBLIC_SITE="${PUBLIC_ROOT}/site"
PUBLIC_DB="${PUBLIC_SITE}/greek.db"
PUBLIC_PREVIOUS="${PUBLIC_ROOT}/greek.db.previous"
PUBLIC_INCOMING="${PUBLIC_ROOT}/incoming"
PUBLIC_RELEASES="${PUBLIC_ROOT}/releases"
PUBLIC_COMPOSE_ENV="${PUBLIC_ROOT}/compose.env"
PUBLIC_TUNNEL_ENV="${PUBLIC_ROOT}/tunnel.env"
PUBLIC_COMPOSE_FILE="/opt/ancient-greek-reader/app/deploy/public/docker-compose.yml"
MIGRATIONS_DIR="/opt/ancient-greek-reader/app/greek/db/migrations"
READER_IMAGE="ancient-greek-reader:latest"
# One deploy or publish at a time: both restart the public site, and a
# publish exports from the private reader, which a deploy restarts
SITE_LOCK="/run/lock/ancient-greek-reader.lock"

public_compose() {
  docker compose --env-file "${PUBLIC_COMPOSE_ENV}" -f "${PUBLIC_COMPOSE_FILE}" "$@"
}

take_site_lock() {
  exec 9>"${SITE_LOCK}"
  flock -w 900 9 || die "Another deploy or publish has held ${SITE_LOCK} for 15 minutes"
}

# The site's own user (it owns the published copy and runs the web app; it
# can't log in), its folders, and the settings compose reads.
ensure_public_site() {
  if ! id -u "${PUBLIC_USER}" >/dev/null 2>&1; then
    log "Creating the public site's user ${PUBLIC_USER}"
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin "${PUBLIC_USER}"
  fi
  install -d -m 755 -o root -g root "${PUBLIC_ROOT}" "${PUBLIC_RELEASES}"
  install -d -m 755 -o "${PUBLIC_USER}" -g "${PUBLIC_USER}" "${PUBLIC_SITE}"
  install -d -m 700 -o "${PUBLIC_USER}" -g "${PUBLIC_USER}" "${PUBLIC_INCOMING}"
  local settings
  settings="$(mktemp)"
  {
    printf 'PUBLIC_UID=%s\n' "$(id -u "${PUBLIC_USER}")"
    printf 'PUBLIC_GID=%s\n' "$(id -g "${PUBLIC_USER}")"
    printf 'PUBLIC_SITE=%s\n' "${PUBLIC_SITE}"
    printf 'PUBLIC_ROOT=%s\n' "${PUBLIC_ROOT}"
  } > "${settings}"
  install -m 644 -o root -g root "${settings}" "${PUBLIC_COMPOSE_ENV}"
  rm -f "${settings}"
}

# This code's last migration, e.g. 020_reading_app
code_migration() {
  basename "$(find "${MIGRATIONS_DIR}" -name '*.sql' | sort | tail -n 1)" .sql
}

# The last migration a database file has had, read without changing it
database_migration() {
  python3 - "$1" <<'PY'
import sqlite3, sys
from pathlib import Path
conn = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
print(Path(conn.execute("SELECT max(name) FROM _migrations").fetchone()[0]).stem)
PY
}

# Make a new copy one file, as the read-only mount needs (no WAL), and check
# it: integrity_check, and this code's last migration.
check_new_copy() {
  local db="$1" migration
  migration="$(code_migration)"
  python3 - "${db}" "${migration}" <<'PY' || die "${db} is not fit to publish"
import sqlite3, sys
from pathlib import Path
path, migration = sys.argv[1:]
conn = sqlite3.connect(path)
mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
check = conn.execute("PRAGMA integrity_check").fetchone()[0]
last = Path(conn.execute("SELECT max(name) FROM _migrations").fetchone()[0]).stem
conn.close()
problems = []
if mode != "delete":
    problems.append(f"its journal mode stays {mode}")
if check != "ok":
    problems.append(f"integrity_check: {check}")
if last != migration:
    problems.append(f"its last migration is {last}, this code's {migration}")
if problems:
    sys.exit(f"{path}: " + "; ".join(problems))
PY
  for end in journal wal shm; do
    [[ ! -e "${db}-${end}" ]] || die "${db}-${end} is left beside it"
  done
}

# GET a path from inside the public web app's container: it has no port on
# the Pi
public_get() {
  public_compose exec -T web python -c '
import sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8999" + sys.argv[1], timeout=30) as r:
    sys.stdout.write(r.read().decode())
' "$1"
}

start_public_site() {
  local tunnel=()
  if [[ -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    tunnel=(--profile tunnel)
  fi
  log "Starting the public site${tunnel[*]:+ and its tunnel}"
  public_compose "${tunnel[@]}" up -d --remove-orphans
}

# The public web app answers /version with this commit's code, serving a
# database with every migration, and the Library loads.
check_public_site() {
  local commit="$1" attempt version
  for attempt in $(seq 1 30); do
    version="$(public_get /version 2>/dev/null)" && break
    log "Waiting for the public site (attempt ${attempt}/30)"
    sleep 2
  done
  [[ -n "${version:-}" ]] || { public_compose ps -a; return 1; }
  python3 - "${version}" "${commit}" <<'PY' || return 1
import json, sys
v, commit = json.loads(sys.argv[1]), sys.argv[2][:7]
code, database = v["code"]["commit"], v["database"]
problems = []
if code != commit:
    problems.append(f"it runs {code}, not {commit}")
if not v["serving"]:
    problems.append("it can't use its database")
if not database["version"] or database["pending"]:
    problems.append(f"the database is not migrated: {database}")
if problems:
    sys.exit("The public site's /version: " + "; ".join(problems))
print(f"The public site's /version: code {code}, database {database['version']}")
PY
  public_get /library >/dev/null || { log "The public Library does not load"; return 1; }
}

# Put a checked copy in the site's place, keeping the one it replaces as
# greek.db.previous, and put that back if the site then fails its checks.
swap_in() {
  local new="$1" commit="$2"
  chown "${PUBLIC_USER}:${PUBLIC_USER}" "${new}"
  chmod 444 "${new}"
  public_compose stop web >/dev/null 2>&1 || true
  if [[ -f "${PUBLIC_DB}" ]]; then
    mv -f "${PUBLIC_DB}" "${PUBLIC_PREVIOUS}"
  fi
  mv "${new}" "${PUBLIC_DB}"
  start_public_site
  if check_public_site "${commit}"; then
    return 0
  fi
  public_compose stop web >/dev/null 2>&1 || true
  mv -f "${PUBLIC_DB}" "${PUBLIC_INCOMING}/greek.db.failed"
  if [[ -f "${PUBLIC_PREVIOUS}" ]]; then
    log "The new copy failed its checks: putting the previous one back"
    mv "${PUBLIC_PREVIOUS}" "${PUBLIC_DB}"
    start_public_site
  fi
  die "The public site failed its checks with the new copy (kept as ${PUBLIC_INCOMING}/greek.db.failed)"
}

# New code with a migration the published copy lacks: migrate a copy of it
# with `greek migrate` (no network, as the site's user) and swap that in, so
# the site needn't wait for a new publish.
migrate_published_copy() {
  local commit="$1" have want
  have="$(database_migration "${PUBLIC_DB}")"
  want="$(code_migration)"
  [[ "${have}" != "${want}" ]] || return 0
  log "The published copy is at ${have}, this code at ${want}: migrating a copy"
  rm -rf "${PUBLIC_INCOMING:?}"/*
  install -m 600 -o "${PUBLIC_USER}" -g "${PUBLIC_USER}" "${PUBLIC_DB}" "${PUBLIC_INCOMING}/greek.db"
  docker run --rm --network none \
    --user "$(id -u "${PUBLIC_USER}"):$(id -g "${PUBLIC_USER}")" \
    -e HOME=/tmp -v "${PUBLIC_INCOMING}:/data" "${READER_IMAGE}" migrate \
    || die "greek migrate failed on a copy of the published database"
  # The copy migrate made first: the previous copy is kept anyway
  rm -f "${PUBLIC_INCOMING}"/greek-backup-*.db
  check_new_copy "${PUBLIC_INCOMING}/greek.db"
  swap_in "${PUBLIC_INCOMING}/greek.db" "${commit}"
}
