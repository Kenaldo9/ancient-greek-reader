#!/usr/bin/env bash
# The public site's way in, Cloudflare Tunnel's cloudflared (deploy/README.md,
# "The tunnel"). From your computer:
#   ssh -t PI sudo /opt/ancient-greek-reader/app/deploy/tunnel.sh on|off|status
# on: asks for the tunnel's token the first time (or with --new-token) and
# keeps it in public/tunnel.env, loads the firewall that walls the tunnel off
# from the Pi, the home network and the tailnet, checks it, and starts
# cloudflared; deploys and publishes then keep it running. off: stops
# cloudflared and sets the token aside, so nothing starts it until `on`.
set -euo pipefail

NEW_TOKEN="false"
ACTION=""

usage() {
  cat <<USAGE
Usage: sudo deploy/tunnel.sh on [--new-token] | off | status

  on            Start the tunnel: the token saved before, or ask for one
  --new-token   Ask for a new token even if one is saved
  off           Stop the tunnel and set its token aside
  status        Whether the tunnel runs, and its connections to Cloudflare
  -h, --help    Show this help
USAGE
}

log() {
  printf '[tunnel] %s\n' "$*"
}

die() {
  printf '[tunnel][error] %s\n' "$*" >&2
  exit 1
}

# shellcheck source=public/common.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/public/common.sh"

# A tunnel token is base64 of JSON with the account (a), the tunnel (t) and
# its secret (s). Read from stdin, so it never shows in a process list.
token_is_valid() {
  python3 -c '
import base64, binascii, json, sys
text = sys.stdin.read().strip()
try:
    token = json.loads(base64.b64decode(text + "=" * (-len(text) % 4), validate=True))
except (binascii.Error, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(token, dict) and {"a", "t", "s"} <= token.keys() else 1)
'
}

ask_for_token() {
  local token
  [[ -t 0 ]] || die "It asks for the token: run it from a terminal (ssh -t)"
  read -r -s -p "The tunnel's token (the last word of its install command): " token
  printf '\n'
  token_is_valid <<<"${token}" || die "That is not a Cloudflare Tunnel token"
  (umask 077 && printf 'TUNNEL_TOKEN=%s\n' "${token}" >"${PUBLIC_TUNNEL_ENV}.new")
  mv -f "${PUBLIC_TUNNEL_ENV}.new" "${PUBLIC_TUNNEL_ENV}"
  rm -f "${PUBLIC_TUNNEL_OFF}"
  log "Saved the token in ${PUBLIC_TUNNEL_ENV}"
}

tunnel_on() {
  [[ -f "${PUBLIC_DB}" ]] || die "Nothing is published yet: run deploy/publish.sh first"
  take_site_lock
  if [[ "${NEW_TOKEN}" == "true" ]]; then
    ask_for_token
  elif [[ ! -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    if [[ -s "${PUBLIC_TUNNEL_OFF}" ]]; then
      log "Using the token set aside by 'tunnel.sh off'"
      mv -f "${PUBLIC_TUNNEL_OFF}" "${PUBLIC_TUNNEL_ENV}"
    else
      ask_for_token
    fi
  fi
  start_public_site
  log "The tunnel runs. Its connections: sudo $0 status"
}

tunnel_off() {
  take_site_lock
  cloudflared_container | xargs -r docker rm -f >/dev/null
  if [[ -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    mv -f "${PUBLIC_TUNNEL_ENV}" "${PUBLIC_TUNNEL_OFF}"
  fi
  log "The tunnel is off: nothing reaches the public site. 'tunnel.sh on' starts it again."
}

tunnel_status() {
  local container state
  if [[ -s "${PUBLIC_TUNNEL_ENV}" ]]; then
    log "Token: saved (${PUBLIC_TUNNEL_ENV})"
  elif [[ -s "${PUBLIC_TUNNEL_OFF}" ]]; then
    log "Token: set aside by 'tunnel.sh off'"
  else
    log "Token: none yet ('tunnel.sh on' asks for one)"
  fi
  if nft list table inet greek_public >/dev/null 2>&1; then
    log "Firewall: loaded"
  else
    log "Firewall: not loaded"
  fi
  container="$(cloudflared_container)"
  if [[ -z "${container}" ]]; then
    log "cloudflared: no container"
    return
  fi
  state="$(docker inspect --format '{{.State.Status}} since {{.State.StartedAt}}' "${container}")"
  log "cloudflared: ${state}"
  log "Its connections to Cloudflare in the last day:"
  docker logs --since 24h "${container}" 2>&1 \
    | grep -E 'Registered tunnel connection|ERR' | tail -n 8 || true
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      on | off | status) ACTION="$1"; shift ;;
      --new-token) NEW_TOKEN="true"; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
  [[ -n "${ACTION}" ]] || { usage; exit 1; }
  [[ "${NEW_TOKEN}" == "false" || "${ACTION}" == "on" ]] || die "--new-token goes with 'on'"
}

main() {
  parse_args "$@"
  [[ "${EUID}" -eq 0 ]] || die "Run as root (sudo deploy/tunnel.sh ...)"
  case "${ACTION}" in
    on) ensure_public_site; tunnel_on ;;
    off) tunnel_off ;;
    status) tunnel_status ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
