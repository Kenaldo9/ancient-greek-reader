#!/usr/bin/env bash
# One-time root setup on the Raspberry Pi before the first deploy. Safe to
# run again. Copy it and the GitHub Actions login key's public half to the
# Pi, then from your own terminal:
#   ssh -t PI 'sudo bash /tmp/pi_setup.sh --gha-pubkey /tmp/ancient-greek-reader-gha.pub'
# It creates the service user and /opt/ancient-greek-reader/{app,data}, and
# lets gha-deploy log in with the new key and run exactly the deploy command
# as root. The repository is public, so the Pi clones it over HTTPS with no
# key. The Pi needs Tailscale up and a gha-deploy login user (deploy/README.md).
set -euo pipefail

SERVICE_USER="greekreader"
APP_ROOT="/opt/ancient-greek-reader"
DEPLOY_USER="gha-deploy"
SUDOERS_FILE="/etc/sudoers.d/gha-deploy-ancient-greek-reader"
DEPLOY_COMMAND="${APP_ROOT}/app/deploy/deploy_pi.sh --non-interactive"

GHA_PUBKEY_FILE=""

log() {
  printf '[setup] %s\n' "$*"
}

die() {
  printf '[setup][error] %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gha-pubkey) GHA_PUBKEY_FILE="${2:?--gha-pubkey needs a file}"; shift 2 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || die "Run as root"
[[ -f "${GHA_PUBKEY_FILE}" ]] || die "Pass the GitHub Actions public key with --gha-pubkey FILE"
gha_pubkey="$(<"${GHA_PUBKEY_FILE}")"
[[ "${gha_pubkey}" == ssh-ed25519\ * ]] || die "${GHA_PUBKEY_FILE} is not an ed25519 public key"
id -u "${DEPLOY_USER}" >/dev/null 2>&1 \
  || die "User ${DEPLOY_USER} does not exist; create it first (deploy/README.md)"
tailscale status >/dev/null 2>&1 || die "Tailscale is not up (deploy/README.md)"
log "On the tailnet as $(tailscale status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  log "Creating service user ${SERVICE_USER}"
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

log "Creating ${APP_ROOT}"
install -d -m 755 -o root -g root "${APP_ROOT}"
install -d -m 755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_ROOT}/app"
install -d -m 755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_ROOT}/data"

# Lock the key to this app's deploy command: a leaked key can then only start
# that deploy (no shell, no tunnels, no terminal). Lines are matched on the key
# itself, which may already carry options, so a re-run replaces them instead
# of adding an unlocked duplicate.
log "Letting ${DEPLOY_USER} log in with the new key, locked to its deploy command"
deploy_ssh_dir="$(getent passwd "${DEPLOY_USER}" | cut -d: -f6)/.ssh"
install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${deploy_ssh_dir}"
authorized_keys="${deploy_ssh_dir}/authorized_keys"
touch "${authorized_keys}"
key_blob="$(awk '{print $1 " " $2}' <<<"${gha_pubkey}")"
keys_tmp="$(mktemp)"
awk -v blob="${key_blob}" -v locked="restrict,command=\"sudo -n ${DEPLOY_COMMAND}\" ${gha_pubkey}" '
  index($0, blob) { if (!done) print locked; done = 1; next }
  { print }
  END { if (!done) print locked }
' "${authorized_keys}" > "${keys_tmp}"
install -m 600 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${keys_tmp}" "${authorized_keys}"
rm -f "${keys_tmp}" "${GHA_PUBKEY_FILE}"

log "Allowing ${DEPLOY_USER} to run exactly: ${DEPLOY_COMMAND}"
sudoers_tmp="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s\n' "${DEPLOY_USER}" "${DEPLOY_COMMAND}" > "${sudoers_tmp}"
visudo -cf "${sudoers_tmp}" >/dev/null || die "The sudoers rule did not validate"
install -m 440 -o root -g root "${sudoers_tmp}" "${SUDOERS_FILE}"
rm -f "${sudoers_tmp}"

cat <<DONE

Setup complete. Now set the repository's deploy secrets and do the first
deploy (deploy/README.md).
DONE
