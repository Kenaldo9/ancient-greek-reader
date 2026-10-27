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
# Where deploy/tunnel.sh off sets the token aside (deploy_pi.sh reads it too)
# shellcheck disable=SC2034
PUBLIC_TUNNEL_OFF="${PUBLIC_ROOT}/tunnel.env.off"
PUBLIC_FILES="/opt/ancient-greek-reader/app/deploy/public"
PUBLIC_COMPOSE_FILE="${PUBLIC_FILES}/docker-compose.yml"
# The tunnel's network and its bridge, the site's own network
# (docker-compose.yml), and the firewall that walls them off (firewall.nft),
# installed outside the checkout
PUBLIC_OUT_NETWORK="greek-public_out"
PUBLIC_OUT_BRIDGE="greek-out"
PUBLIC_SITE_NETWORK="greek-public_site"
PUBLIC_FIREWALL_RULES="/etc/ancient-greek-reader/public-firewall.nft"
PUBLIC_FIREWALL_CLOSED="/etc/ancient-greek-reader/public-firewall-closed.nft"
PUBLIC_FIREWALL_UNIT="greek-public-firewall.service"
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

# The firewall that keeps the tunnel's network off the Pi, the home network
# and the tailnet (firewall.nft): copied out of the checkout, loaded now and
# at every boot before Docker starts the containers, or if it fails to load
# there, firewall-closed.nft, which keeps the site's networks off everything.
ensure_public_firewall() {
  install -D -m 644 -o root -g root "${PUBLIC_FILES}/firewall.nft" "${PUBLIC_FIREWALL_RULES}"
  install -m 644 -o root -g root "${PUBLIC_FILES}/firewall-closed.nft" "${PUBLIC_FIREWALL_CLOSED}"
  install -m 644 -o root -g root "${PUBLIC_FILES}/${PUBLIC_FIREWALL_UNIT}" \
    "/etc/systemd/system/${PUBLIC_FIREWALL_UNIT}"
  systemctl daemon-reload
  systemctl enable --quiet "${PUBLIC_FIREWALL_UNIT}"
  systemctl restart "${PUBLIC_FIREWALL_UNIT}" \
    || die "The tunnel's firewall failed to load (systemctl status ${PUBLIC_FIREWALL_UNIT})"
  nft list table inet greek_public >/dev/null || die "The tunnel's firewall is not loaded"
}

# The IPv4 address of a network device of the Pi's, if it has one
device_address() {
  ip -4 -o addr show dev "$1" 2>/dev/null | awk '{ sub("/.*", "", $4); print $4; exit }'
}

# The nameservers on the home network in the resolv.conf files given, once
# each: private IPv4 addresses, not loopback (a local stub such as
# systemd-resolved's 127.0.0.53) and not servers on the internet
home_dns_servers() {
  cat "$@" 2>/dev/null | awk '$1 == "nameserver" { print $2 }' | python3 -c '
import ipaddress, sys
seen = set()
for line in sys.stdin:
    try:
        a = ipaddress.ip_address(line.strip())
    except ValueError:
        continue
    if a.version == 4 and not a.is_global and not a.is_loopback and a not in seen:
        seen.add(a)
        print(a)
'
}

# A throwaway container on both of cloudflared's networks tries the Pi's
# addresses (the site network's gateway too: an internal network still
# reaches the Pi), the home router, the DNS servers the Pi asks and the
# tailnet's, which must not answer, and an address on the internet, which
# must (probe_walls.py). The caller runs it in an `if`, where set -e is off.
check_tunnel_walls() {
  local walled=() address device probe dns=0
  address="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
  [[ -z "${address}" ]] || walled+=("the Pi's home network address=${address}:22")
  address="$(ip -4 route show default | awk '{ print $3; exit }')"
  [[ -z "${address}" ]] || walled+=("the home router=${address}:80")
  address="$(tailscale ip -4 2>/dev/null | head -n 1)"
  [[ -z "${address}" ]] || walled+=("the Pi's tailnet address=${address}:22")
  walled+=("the tailnet's DNS=100.100.100.100:53")
  for device in "${PUBLIC_OUT_BRIDGE}" docker0; do
    address="$(device_address "${device}")"
    [[ -z "${address}" ]] || walled+=("the Pi on ${device}=${address}:22")
  done
  # Docker's own record, whatever its bridge is called
  address="$(docker network inspect --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' \
    "${PUBLIC_SITE_NETWORK}")"
  [[ -n "${address}" ]] || { log "The site's network ${PUBLIC_SITE_NETWORK} has no gateway"; return 1; }
  walled+=("the Pi on the site's network=${address}:22")
  # The Pi's own DNS servers on the home network: systemd-resolved's list,
  # or without it the system's own
  while read -r address; do
    walled+=("the home network's DNS server=${address}:53")
    dns=$((dns + 1))
  done < <(home_dns_servers /run/systemd/resolve/resolv.conf /etc/resolv.conf)
  ((dns > 0)) || log "No DNS server on the home network to try: none in /run/systemd/resolve/resolv.conf or /etc/resolv.conf"
  # Where cloudflared is: on the tunnel's network, and joined to the site's
  probe="$(docker create --rm --network "${PUBLIC_OUT_NETWORK}" --user 65534:65534 \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    -v "${PUBLIC_FILES}/probe_walls.py:/probe_walls.py:ro" \
    --entrypoint python "${READER_IMAGE}" /probe_walls.py \
    --open "the internet=1.1.1.1:443" "${walled[@]}")" || return 1
  if ! docker network connect "${PUBLIC_SITE_NETWORK}" "${probe}"; then
    docker rm -f "${probe}" >/dev/null
    return 1
  fi
  docker start --attach "${probe}"
}

# The web app, and with a token in tunnel.env the tunnel: its network is
# made and checked first, so cloudflared never runs without its walls.
start_public_site() {
  local tunnel=()
  if [[ -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    tunnel=(--profile tunnel)
    ensure_public_firewall
    public_compose "${tunnel[@]}" up --no-start
    if ! check_tunnel_walls; then
      cloudflared_container | xargs -r docker rm -f >/dev/null
      die "The tunnel's network is not walled off (deploy/public/firewall.nft): cloudflared stays stopped"
    fi
  fi
  log "Starting the public site${tunnel[*]:+ and its tunnel}"
  public_compose "${tunnel[@]}" up -d --remove-orphans
}

# cloudflared's container, if there is one, found without compose (which
# refuses the tunnel's profile when tunnel.env is missing)
cloudflared_container() {
  docker ps -aq --filter label=com.docker.compose.project=greek-public \
    --filter label=com.docker.compose.service=cloudflared
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
