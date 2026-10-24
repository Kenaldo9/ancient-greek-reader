#!/usr/bin/env bash
# Deploy the reader to the Raspberry Pi: pull main, build the image, restart
# the web app, publish it on the tailnet with `tailscale serve`, and check the
# result; then restart the public site on the same image (deploy/public/),
# once it has a published database. GitHub Actions runs
#   sudo -n /opt/ancient-greek-reader/app/deploy/deploy_pi.sh --non-interactive
# after the tests pass on main; the first run is by hand, seeded with a
# database (deploy/README.md). GitHub Actions logs are public in a public
# repository, so a run with --non-interactive prints neither the tailnet's
# name nor the app's logs.
set -euo pipefail

SERVICE_USER="greekreader"
APP_ROOT="/opt/ancient-greek-reader"
APP_DIR="${APP_ROOT}/app"
DATA_DIR="${APP_ROOT}/data"
APP_ENV_FILE="${APP_DIR}/.env"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"
IMAGE="ancient-greek-reader:latest"
TITLE_LABEL="org.opencontainers.image.title=ancient-greek-reader"
# Public: cloned and pulled over HTTPS with no key
REPO_URL="https://github.com/Kenaldo9/ancient-greek-reader.git"
APP_PORT="8030"
# The tailnet's 443 is another app's on this Pi
SERVE_PORT="8443"
SERVE_TARGET="http://127.0.0.1:${APP_PORT}"

BRANCH="main"
APP_ENV_SOURCE=""
SEED_SOURCE=""
NON_INTERACTIVE="false"
DRY_RUN="false"
GIT_COMMIT="unknown"
PUBLIC_STATUS="(dry-run)"

usage() {
  cat <<USAGE
Usage: sudo deploy/deploy_pi.sh [options]

Options:
  --branch NAME          Git branch to deploy (default: main)
  --app-env-file PATH    Install PATH as ${APP_ENV_FILE} (the API key,
                         for greek study only), then delete PATH. Without
                         it, a missing file is asked for interactively.
  --seed-data DIR        Copy DIR (greek.db made with SQLite's .backup, plus
                         sources/, texts/, catalog.json) into ${DATA_DIR}.
                         Only when it has no greek.db yet: never replaces one.
  --non-interactive      Fail instead of asking for anything missing
  --dry-run              Print the steps without changing the host
  -h, --help             Show this help
USAGE
}

log() {
  printf '[deploy] %s\n' "$*"
}

die() {
  printf '[deploy][error] %s\n' "$*" >&2
  exit 1
}

# In a dry run, log what would happen and return 0 so the caller returns.
dry() {
  [[ "${DRY_RUN}" == "true" ]] || return 1
  log "[dry-run] Would $*"
}

as_service_user() {
  runuser -u "${SERVICE_USER}" -- "$@"
}

# The service user, port and data folder come from .env (write_compose_settings).
compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

print_app_diagnostics() {
  log "---- container status ----"
  compose ps -a || true
  # The logs hold the visitors' addresses: keep them out of public logs.
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    log "Container logs: sudo docker compose -f ${COMPOSE_FILE} logs --tail 60 web"
    return
  fi
  log "---- recent container logs ----"
  compose logs --no-color --tail 60 web || true
}

wait_for_app() {
  local attempt
  for attempt in $(seq 1 30); do
    if curl --noproxy '*' -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${APP_PORT}/version"; then
      return 0
    fi
    log "Waiting for the reader on 127.0.0.1:${APP_PORT} (attempt ${attempt}/30)"
    sleep 2
  done
  print_app_diagnostics
  die "The reader did not answer on 127.0.0.1:${APP_PORT}"
}

tailnet_name() {
  tailscale status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

# Whether tailscale serve passes the tailnet's SERVE_PORT on to SERVE_TARGET.
serve_is_set() {
  tailscale serve status --json | python3 -c '
import json, sys
port, target = sys.argv[1], sys.argv[2]
web = json.load(sys.stdin).get("Web") or {}
sys.exit(0 if any(
    host.endswith(":" + port)
    and (conf.get("Handlers") or {}).get("/", {}).get("Proxy") == target
    for host, conf in web.items()
) else 1)
' "${SERVE_PORT}" "${SERVE_TARGET}"
}

# /version answers with this commit's code, serving (not refusing a newer
# database), on a database with every migration applied.
version_is_deployed() {
  curl --noproxy '*' -fsS --max-time 10 "http://127.0.0.1:${APP_PORT}/version" \
    | python3 -c '
import json, sys
v, commit = json.load(sys.stdin), sys.argv[1][:7]
code, database = v["code"]["commit"], v["database"]
migration = database["version"]
problems = []
if code != commit:
    problems.append(f"it runs {code}, not {commit}")
if not v["serving"]:
    problems.append("the database is newer than the code")
if not migration or database["pending"]:
    problems.append(f"the database is not migrated: {database}")
if problems:
    sys.exit("/version: " + "; ".join(problems))
print(f"[deploy] /version: code {code}, database {migration}")
' "${GIT_COMMIT}"
}

require_root() {
  dry "require root" && return
  [[ "${EUID}" -eq 0 ]] || die "Run as root (sudo deploy/deploy_pi.sh ...)"
}

check_prereqs() {
  dry "check docker, docker compose, git, curl, python3, tailscale and nft" && return
  local cmd
  for cmd in docker git curl python3 runuser tailscale flock nft systemctl; do
    command -v "${cmd}" >/dev/null 2>&1 || die "${cmd} not found"
  done
  docker compose version >/dev/null 2>&1 || die "'docker compose' plugin not found"
  tailscale status >/dev/null 2>&1 \
    || die "Tailscale is not up (deploy/README.md)"
}

ensure_service_user() {
  dry "ensure service user ${SERVICE_USER} exists" && return
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    log "Creating service user ${SERVICE_USER}"
    useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi
}

ensure_directories() {
  dry "create ${APP_DIR} and ${DATA_DIR}" && return
  install -d -m 755 -o root -g root "${APP_ROOT}"
  install -d -m 755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_DIR}"
  install -d -m 755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${DATA_DIR}"
}

sync_repository() {
  dry "clone or fast-forward ${APP_DIR} to origin/${BRANCH}" && return
  if [[ ! -d "${APP_DIR}/.git" ]]; then
    [[ -z "$(ls -A "${APP_DIR}")" ]] || die "${APP_DIR} is not empty and not a git checkout"
    log "Cloning ${REPO_URL} (${BRANCH})"
    as_service_user git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
  else
    log "Updating ${APP_DIR} to origin/${BRANCH}"
    as_service_user git -C "${APP_DIR}" fetch --prune origin
    as_service_user git -C "${APP_DIR}" checkout "${BRANCH}"
    as_service_user git -C "${APP_DIR}" merge --ff-only "origin/${BRANCH}"
  fi
  GIT_COMMIT="$(as_service_user git -C "${APP_DIR}" rev-parse HEAD)"
}

# A pull can change this script, but bash keeps running the version it
# started. Re-run the checked-out copy so a deploy always uses the deploy
# logic of the commit it deploys (also covers a first run started from a copy
# outside the checkout). Compare checksums taken before and after the pull:
# when this runs from the checkout, ${BASH_SOURCE[0]} already names the new
# file, so comparing the two paths would always match.
run_checked_out_script() {
  [[ "${DRY_RUN}" == "true" || "${GREEK_DEPLOY_SYNCED:-}" == "1" ]] && return
  local checked_out="${APP_DIR}/deploy/deploy_pi.sh"
  if [[ "$(sha256sum < "${checked_out}")" != "${STARTED_SCRIPT_SHA}" ]]; then
    log "Continuing with the checked-out deploy script"
    GREEK_DEPLOY_SYNCED=1 exec bash "${checked_out}" "${ORIGINAL_ARGS[@]}"
  fi
}

# The public site's steps (deploy/public/common.sh), as checked out, and the
# lock a publish takes too: a deploy restarts the reader it exports from.
load_public_site_steps() {
  local steps="${APP_DIR}/deploy/public/common.sh"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] Would load ${steps} and wait for ${SITE_LOCK:-the site lock}"
    return
  fi
  # shellcheck source=public/common.sh
  source "${steps}"
  take_site_lock
}

# The reader needs no key; greek study's batches do.
write_app_env_interactively() {
  local key
  read -r -s -p "ANTHROPIC_API_KEY (for greek study; Enter for none): " key
  printf '\n'
  (
    umask 077
    if [[ -n "${key}" ]]; then
      printf 'ANTHROPIC_API_KEY=%s\n' "${key}" > "${APP_ENV_FILE}"
    else
      : > "${APP_ENV_FILE}"
    fi
  )
}

ensure_app_env() {
  dry "create ${APP_ENV_FILE} if missing (or install --app-env-file)" && return
  if [[ -n "${APP_ENV_SOURCE}" ]]; then
    [[ -f "${APP_ENV_SOURCE}" ]] || die "No such file: ${APP_ENV_SOURCE}"
    log "Installing ${APP_ENV_SOURCE} as ${APP_ENV_FILE}"
    install -m 600 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_ENV_SOURCE}" "${APP_ENV_FILE}"
    shred -u "${APP_ENV_SOURCE}" 2>/dev/null || rm -f "${APP_ENV_SOURCE}"
  elif [[ ! -f "${APP_ENV_FILE}" ]]; then
    [[ "${NON_INTERACTIVE}" == "false" ]] \
      || die "${APP_ENV_FILE} is missing; run the first deploy by hand (deploy/README.md)"
    log "Creating ${APP_ENV_FILE}"
    write_app_env_interactively
  fi
  # The image's GREEK_DATA_DIR=/data is the mounted data folder; another
  # value would quietly give the app an empty database inside the container.
  ! grep -q '^GREEK_DATA_DIR=' "${APP_ENV_FILE}" \
    || die "${APP_ENV_FILE} sets GREEK_DATA_DIR; remove it (the container uses /data)"
  write_compose_settings
}

# docker-compose.yml's defaults are a computer's own (./data, port 8999, uid
# 1000). Compose reads .env as well, so with the Pi's values there a plain
# `sudo docker compose up -d` by hand runs the app as a deploy does, instead
# of on an empty database in app/data.
write_compose_settings() {
  local settings
  settings="$(mktemp)"
  grep -v -E '^(GREEK_UID|GREEK_GID|GREEK_PORT|GREEK_DATA)=' "${APP_ENV_FILE}" > "${settings}" || true
  {
    printf 'GREEK_UID=%s\n' "$(id -u "${SERVICE_USER}")"
    printf 'GREEK_GID=%s\n' "$(id -g "${SERVICE_USER}")"
    printf 'GREEK_PORT=%s\n' "${APP_PORT}"
    printf 'GREEK_DATA=%s\n' "${DATA_DIR}"
  } >> "${settings}"
  install -m 600 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${settings}" "${APP_ENV_FILE}"
  rm -f "${settings}"
}

seed_data() {
  local db="${SEED_SOURCE}/greek.db"
  [[ -f "${db}" ]] || die "${SEED_SOURCE} has no greek.db"
  [[ ! -e "${SEED_SOURCE}/greek.db-wal" ]] \
    || die "${SEED_SOURCE} has a greek.db-wal: make the copy with sqlite3's .backup"
  log "Checking ${db}"
  python3 - "${db}" <<'PY' || die "${db} failed SQLite's integrity check"
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?immutable=1", uri=True)  # leaves no -wal/-shm
sys.exit(0 if conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok" else 1)
PY
  log "Copying ${SEED_SOURCE} into ${DATA_DIR}"
  cp -a "${SEED_SOURCE}/." "${DATA_DIR}/"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"
  # cp -a gives DATA_DIR the seed folder's mode; keep it readable for the
  # Pi's own login
  chmod 755 "${DATA_DIR}"
}

# The app makes an empty database when it finds none, so it never starts
# without the real one in place.
ensure_database() {
  dry "check ${DATA_DIR}/greek.db exists (or seed it with --seed-data)" && return
  if [[ -e "${DATA_DIR}/greek.db" ]]; then
    [[ -z "${SEED_SOURCE}" ]] \
      || die "${DATA_DIR}/greek.db already exists; --seed-data never replaces a database"
    return
  fi
  [[ -n "${SEED_SOURCE}" ]] \
    || die "No database at ${DATA_DIR}/greek.db; seed it with --seed-data (deploy/README.md)"
  seed_data
}

build_and_start() {
  dry "build ${IMAGE} for the checked-out commit and (re)start the web app" && return
  log "Building ${IMAGE} for ${GIT_COMMIT}"
  compose build web
  log "Starting the web app"
  compose up -d --remove-orphans web
}

# The serve setting lives in tailscaled's state and survives reboots; setting
# it on every deploy keeps it right if it was changed by hand.
configure_tailscale_serve() {
  dry "publish ${SERVE_TARGET} on the tailnet with tailscale serve (HTTPS, port ${SERVE_PORT})" && return
  serve_is_set && return
  log "Publishing ${SERVE_TARGET} on the tailnet's port ${SERVE_PORT}"
  tailscale serve --bg --yes --https="${SERVE_PORT}" "${SERVE_TARGET}"
}

run_health_checks() {
  dry "check the reader runs this commit's image on its database, and tailscale serve" && return
  log "Running health checks"
  wait_for_app

  version_is_deployed \
    || { print_app_diagnostics; die "The reader is not the version just deployed"; }
  curl --noproxy '*' -fsS --max-time 30 -o /dev/null "http://127.0.0.1:${APP_PORT}/library" \
    || { print_app_diagnostics; die "The library page does not load"; }

  local image_id container
  image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
  container="$(compose ps -q web)"
  [[ -n "${container}" ]] || { print_app_diagnostics; die "The web container is not running"; }
  [[ "$(docker inspect --format '{{.State.Running}}' "${container}")" == "true" ]] \
    || { print_app_diagnostics; die "The web container is not running"; }
  [[ "$(docker inspect --format '{{.Image}}' "${container}")" == "${image_id}" ]] \
    || die "The web container does not run the image just built"

  # Connections from the Pi itself to its tailnet name can reach another web
  # server on the Pi instead of tailscale serve, so the tailnet side is
  # checked by its setting here and from another device by hand
  # (deploy/README.md).
  serve_is_set || die "tailscale serve does not pass port ${SERVE_PORT} to ${SERVE_TARGET}"
}

# The same image on the published copy of the database, migrated first when
# this code brings a migration. Before the first publish there is nothing to
# start.
deploy_public_site() {
  dry "start the public site on its published database and check it" && return
  ensure_public_site
  if [[ ! -f "${PUBLIC_DB}" ]]; then
    log "The public site has no published database yet: nothing to start"
    PUBLIC_STATUS="none published yet (sudo ${APP_DIR}/deploy/publish.sh)"
    return
  fi
  migrate_published_copy "${GIT_COMMIT}"
  start_public_site
  check_public_site "${GIT_COMMIT}" || die "The public site is not the version just deployed"
  PUBLIC_STATUS="running on $(database_migration "${PUBLIC_DB}")"
  if [[ -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    PUBLIC_STATUS+=", the tunnel on and walled off"
  elif [[ -s "${PUBLIC_TUNNEL_OFF}" ]]; then
    PUBLIC_STATUS+=", the tunnel off (sudo ${APP_DIR}/deploy/tunnel.sh on)"
  else
    PUBLIC_STATUS+=", no tunnel yet"
  fi
}

prune_images() {
  dry "remove old untagged reader images" && return
  docker image prune -f --filter "label=${TITLE_LABEL}" >/dev/null
}

print_summary() {
  local created="(dry-run)" site="(dry-run)"
  if [[ "${DRY_RUN}" == "false" ]]; then
    # In UTC: the Pi's own offset would tell a public log its time zone
    created="$(date -u -d "$(docker image inspect --format '{{.Created}}' "${IMAGE}")" '+%Y-%m-%d %H:%M UTC')"
    site="port ${SERVE_PORT} of this Pi's tailnet name"
    [[ "${NON_INTERACTIVE}" == "true" ]] || site="https://$(tailnet_name):${SERVE_PORT}/"
  fi
  cat <<SUMMARY

Deployment complete.
- Reader (tailnet only): ${site}
- Commit:                ${GIT_COMMIT}
- Image created:         ${created}
- Data:                  ${DATA_DIR}
- Public site:           ${PUBLIC_STATUS}

Useful commands:
- docker exec -it ancient-greek-reader-web-1 python greek.py --version
- sudo docker compose -f ${COMPOSE_FILE} logs -f web
- tailscale serve status
- sudo docker compose --env-file /opt/ancient-greek-reader/public/compose.env \
    -f ${APP_DIR}/deploy/public/docker-compose.yml ps
SUMMARY
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
      --app-env-file) APP_ENV_SOURCE="${2:?--app-env-file needs a path}"; shift 2 ;;
      --seed-data) SEED_SOURCE="${2:?--seed-data needs a folder}"; shift 2 ;;
      --non-interactive) NON_INTERACTIVE="true"; shift ;;
      --dry-run) DRY_RUN="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
}

main() {
  ORIGINAL_ARGS=("$@")
  STARTED_SCRIPT_SHA="$(sha256sum < "${BASH_SOURCE[0]}")"
  parse_args "$@"
  require_root
  check_prereqs
  ensure_service_user
  ensure_directories
  sync_repository
  run_checked_out_script
  load_public_site_steps
  ensure_app_env
  ensure_database
  build_and_start
  configure_tailscale_serve
  run_health_checks
  deploy_public_site
  prune_images
  print_summary
}

# One line, so bash has read it in full before a pull can replace this file.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; exit $?; fi
