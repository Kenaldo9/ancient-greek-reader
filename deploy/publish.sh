#!/usr/bin/env bash
# Publish the live database to the public site: export it from the private
# reader (greek export-data, without the batches' bookkeeping), check the
# export, and swap it in for the public web app, keeping the copy it replaces.
# From your computer:
#   ssh -t PI sudo /opt/ancient-greek-reader/app/deploy/publish.sh
# --restore puts the previous copy back. The export's files stay in
# public/releases/ for a GitHub Release. deploy/README.md, "The public site".
set -euo pipefail

APP_ROOT="/opt/ancient-greek-reader"
COMPOSE_FILE="${APP_ROOT}/app/docker-compose.yml"
DATA_DIR="${APP_ROOT}/data"
APP_PORT="8030"
RELEASES_KEPT=3

RESTORE="false"

usage() {
  cat <<USAGE
Usage: sudo deploy/publish.sh [--restore]

Without options: export the live database, check it and publish it.
  --restore    Put the copy the last publish replaced back
  -h, --help   Show this help
USAGE
}

log() {
  printf '[publish] %s\n' "$*"
}

die() {
  printf '[publish][error] %s\n' "$*" >&2
  exit 1
}

# shellcheck source=public/common.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/public/common.sh"

# The private reader's /version: the code both sites run
reader_version() {
  curl --noproxy '*' -fsS --max-time 10 "http://127.0.0.1:${APP_PORT}/version" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["code"]["commit"])'
}

# greek export-data in the private reader's container, into its data folder,
# then moved (same disk) to public/releases/
export_live_database() {
  local release="$1" staging=".publish-$$"
  log "Exporting the live database (a few minutes on the Pi)"
  docker compose -f "${COMPOSE_FILE}" exec -T web \
    python greek.py export-data "/data/${staging}" \
    || { rm -rf "${DATA_DIR:?}/${staging}"; die "greek export-data failed"; }
  mv "${DATA_DIR}/${staging}" "${release}"
  chown -R root:root "${release}"
  chmod 755 "${release}"
  chmod 644 "${release}"/*
}

# SHA256SUMS, then the database it decompresses to against the manifest's
# checksum
unpack_release() {
  local release="$1" new="$2"
  (cd "${release}" && sha256sum --quiet -c SHA256SUMS) \
    || die "${release}: the checksum does not match"
  log "Decompressing"
  python3 - "${release}" "${new}" <<'PY' || die "${release}: the database does not match its manifest"
import hashlib, json, lzma, shutil, sys
from pathlib import Path
release, new = Path(sys.argv[1]), Path(sys.argv[2])
manifest = json.loads((release / "manifest.json").read_text())
with lzma.open(release / "greek.db.xz") as src, new.open("wb") as dst:
    shutil.copyfileobj(src, dst, 1 << 20)
digest = hashlib.sha256()
with new.open("rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        digest.update(chunk)
sys.exit(0 if digest.hexdigest() == manifest["database"]["sha256"] else 1)
PY
}

new_release_folder() {
  local base folder n=2
  base="${PUBLIC_RELEASES}/data-$(date +%Y-%m-%d)"
  folder="${base}"
  while [[ -e "${folder}" ]]; do
    folder="${base}-${n}"
    n=$((n + 1))
  done
  printf '%s\n' "${folder}"
}

prune_releases() {
  find "${PUBLIC_RELEASES}" -mindepth 1 -maxdepth 1 -type d -name 'data-*' -printf '%T@ %p\n' \
    | sort -rn | tail -n +$((RELEASES_KEPT + 1)) | cut -d' ' -f2- \
    | while read -r old; do
        log "Removing the old export ${old}"
        rm -rf "${old}"
      done
}

publish() {
  local commit release new
  commit="$(reader_version)" || die "The private reader does not answer on 127.0.0.1:${APP_PORT}"
  release="$(new_release_folder)"
  export_live_database "${release}"
  rm -rf "${PUBLIC_INCOMING:?}"/*
  new="${PUBLIC_INCOMING}/greek.db"
  unpack_release "${release}" "${new}"
  check_new_copy "${new}"
  swap_in "${new}" "${commit}"
  prune_releases
  cat <<SUMMARY

Published.
- Database:  $(database_migration "${PUBLIC_DB}"), exported $(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["exported"])' "${release}/manifest.json")
- Code:      ${commit}
- Export:    ${release} (for a GitHub Release)
- Previous:  ${PUBLIC_PREVIOUS} (sudo deploy/publish.sh --restore puts it back)
SUMMARY
}

restore() {
  local commit
  [[ -f "${PUBLIC_PREVIOUS}" ]] || die "There is no previous copy (${PUBLIC_PREVIOUS})"
  commit="$(reader_version)" || die "The private reader does not answer on 127.0.0.1:${APP_PORT}"
  log "Putting the previous copy back"
  rm -rf "${PUBLIC_INCOMING:?}"/*
  mv "${PUBLIC_PREVIOUS}" "${PUBLIC_INCOMING}/greek.db"
  # an older copy may lack the code's migrations: the site would refuse it
  if [[ "$(database_migration "${PUBLIC_INCOMING}/greek.db")" != "$(code_migration)" ]]; then
    mv "${PUBLIC_INCOMING}/greek.db" "${PUBLIC_PREVIOUS}"
    die "The previous copy lacks this code's migrations; publish again instead"
  fi
  swap_in "${PUBLIC_INCOMING}/greek.db" "${commit}"
  log "Restored; the copy it replaced is now ${PUBLIC_PREVIOUS}"
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --restore) RESTORE="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [[ "${EUID}" -eq 0 ]] || die "Run as root (sudo deploy/publish.sh)"
  take_site_lock
  ensure_public_site
  if [[ "${RESTORE}" == "true" ]]; then
    restore
  else
    publish
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
