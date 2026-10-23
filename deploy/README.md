# Deploying to a Raspberry Pi

This is how the reader is deployed to a Raspberry Pi in Docker, reachable
only over [Tailscale](https://tailscale.com): GitHub Actions deploys every
commit on `main` once the tests and the secret scan pass. Beside it the same
image can serve a public site on a published copy of the database
([The public site](#the-public-site)), reached through Cloudflare Tunnel. Change the names
(user, folders, ports) to suit your own machine. Replace `PI` below with
your ssh name for the Pi and `K` with a scratch folder outside the
repository.

## How it works

- `/opt/ancient-greek-reader/app` is a git checkout of `main`, owned by the
  system user `greekreader`. The repository is public, so it is cloned and
  pulled over HTTPS with no key.
- `/opt/ancient-greek-reader/data` is the data folder, mounted at `/data`:
  `greek.db`, the copies made before migrations, and the files `greek
  study` writes. It is outside the checkout, so git never touches it.
- `docker-compose.yml`'s `web` service runs as `greekreader`, on
  `127.0.0.1:8030` only. The deploy writes the service user's IDs, the port
  and the data folder into `app/.env` (`GREEK_UID`, `GREEK_GID`,
  `GREEK_PORT`, `GREEK_DATA`), so a `docker compose` run by hand uses them
  too.
- `tailscale serve` publishes it at `https://<pi>.<tailnet>.ts.net:8443/`,
  for the tailnet only: no port is opened to the internet.
- `app/.env` on the Pi may hold `ANTHROPIC_API_KEY`, for `greek study`; the
  reader needs none. It is never in GitHub.
- After the tests and the secret scan pass on `main`, and if the
  repository variable `PI_DEPLOY_ENABLED` is `true`, the workflow logs in as
  `gha-deploy` and runs exactly
  `sudo -n /opt/ancient-greek-reader/app/deploy/deploy_pi.sh --non-interactive`.
  The login key is locked to that command in `authorized_keys`
  (`restrict,command="…"`), and sudo allows `gha-deploy` that command only.
  The script pulls, rebuilds, restarts the app, sets `tailscale serve`, and
  checks that `/version` shows this commit serving a fully migrated
  database, that the Library loads, and that the container runs the new
  image.
- Actions logs are public. With `--non-interactive` the script prints
  neither the tailnet's name nor the app's logs (they hold visitors'
  addresses); the secrets (the Pi's public name among them) are masked.
- A deploy that brings a new migration: the app copies the database into
  `data/` before applying it ([Database upgrades](../docs/cli.md#database-upgrades)).
  Those copies (about 200 MB each) stay until removed by hand.

## One-time setup

The Pi needs Docker with the compose plugin, git, curl, python3 and
Tailscale (up), and a login user `gha-deploy` that GitHub's runners can
reach over ssh (`sudo useradd --create-home --shell /bin/bash gha-deploy`).
Location labels: `[LOCAL]` your computer, `[PI]` the Pi, `[GITHUB]`
github.com or `gh`.

1. `[LOCAL]` Create this repository's own GitHub Actions login key (never
   reuse another repository's) and copy it and the setup script over:
   ```bash
   ssh-keygen -t ed25519 -N '' -C gha-deploy-ancient-greek-reader -f $K/ancient-greek-reader-gha
   scp deploy/pi_setup.sh $K/ancient-greek-reader-gha.pub PI:/tmp/
   ```
2. `[PI]` From your own terminal (sudo needs a real one):
   ```bash
   ssh -t PI 'sudo bash /tmp/pi_setup.sh --gha-pubkey /tmp/ancient-greek-reader-gha.pub'
   ```
   It creates `greekreader`, `/opt/ancient-greek-reader/{app,data}` and
   `/etc/sudoers.d/gha-deploy-ancient-greek-reader`, and adds the key to
   `gha-deploy`'s `authorized_keys`, locked to the deploy command. A re-run
   locks the key again instead of adding a copy.
3. `[GITHUB]` Set the secrets (`PI_HOST` is the Pi's public name):
   ```bash
   gh secret set PI_HOST --body <public name>
   gh secret set PI_PORT --body 22
   gh secret set PI_USER --body gha-deploy
   gh secret set PI_SSH_KEY < $K/ancient-greek-reader-gha
   ssh-keyscan -t ed25519,ecdsa,rsa <public name> | gh secret set PI_KNOWN_HOSTS
   shred -u $K/ancient-greek-reader-gha $K/ancient-greek-reader-gha.pub
   ```
   Before pinning, compare every host key type of the public name with the
   Pi's own (`ssh-keyscan` of its LAN address).
4. `[LOCAL]` The seed: the [data release](../docs/data.md#the-data-release)'s
   database, and an `.env` with the API key if you will run `greek study`
   on the Pi:
   ```bash
   mkdir -p $K/greek-seed && cd $K/greek-seed
   curl -fLO https://github.com/Kenaldo9/ancient-greek-reader/releases/latest/download/greek.db.xz
   curl -fLO https://github.com/Kenaldo9/ancient-greek-reader/releases/latest/download/SHA256SUMS
   sha256sum -c SHA256SUMS && xz -d greek.db.xz && rm SHA256SUMS
   cd - && (umask 077; echo 'ANTHROPIC_API_KEY=…' > $K/ancient-greek-reader.env)
   rsync -a $K/greek-seed PI:/tmp/
   scp deploy/deploy_pi.sh $K/ancient-greek-reader.env PI:/tmp/
   shred -u $K/ancient-greek-reader.env
   ```
5. `[PI]` The first deploy, by hand:
   ```bash
   ssh -t PI 'sudo bash /tmp/deploy_pi.sh --app-env-file /tmp/ancient-greek-reader.env --seed-data /tmp/greek-seed'
   ssh PI rm -rf /tmp/greek-seed
   ```
   It clones `main`, continues with the checked-out script, installs `.env`
   (and deletes the copy), checks the seed's database and copies it into
   `data/`, builds, starts, sets `tailscale serve` and checks. It never
   replaces an existing `data/greek.db`. Without `--app-env-file` it asks
   for the key (Enter for none).
6. `[LOCAL]` Open `https://<pi>.<tailnet>.ts.net:8443/` from a device on the
   tailnet, and check that the Pi's port 8030 does not answer from the LAN.
7. `[GITHUB]` Turn deploys on: `gh variable set PI_DEPLOY_ENABLED --body
   true`, then `gh workflow run deploy.yml --ref main` and watch it pass.

## Everyday use

Push to `main`; the deploy follows the tests. Pause deploys with
`gh variable set PI_DEPLOY_ENABLED --body false`.

- Commands on the live database, from your computer (your Pi user in the
  `docker` group):
  ```bash
  ssh PI docker exec ancient-greek-reader-web-1 python greek.py study coverage 12
  ssh -t PI docker exec -it ancient-greek-reader-web-1 python greek.py <command>  # one that asks
  ```
  A deploy restarts the container and stops a command running in it: pause
  deploys during a long one.
- A copy of the database: `ssh PI docker exec ancient-greek-reader-web-1
  python greek.py backup - > greek.db` ([greek backup](../docs/cli.md#greek-backup)).
- Logs: `ssh PI docker logs -f ancient-greek-reader-web-1`.
- A new API key: `sudo -u greekreader nano /opt/ancient-greek-reader/app/.env`,
  then `sudo docker compose -f /opt/ancient-greek-reader/app/docker-compose.yml up -d web`.

## The public site

A second compose project, `greek-public`
([public/docker-compose.yml](public/docker-compose.yml)), runs the same
image for everyone on the internet. It serves a published copy of the
database, not the live one, so new study data shows on the private reader
first and on the public site when you publish it.

- `/opt/ancient-greek-reader/public/` (root's) holds it: `site/greek.db`, the
  published copy (read-only, owned by the system user `greekpublic`, which
  can't log in); `greek.db.previous`, the copy the last publish replaced;
  `releases/data-YYYY-MM-DD/`, the last three exports, the same files as a
  data release; `incoming/`, where a new copy is checked; `compose.env`, the
  settings compose reads, which the scripts write; and from the tunnel's
  setup `tunnel.env` (its token).
- The web container runs as `greekpublic` with a read-only filesystem
  (`/tmp` in memory), no Linux capabilities, 512 MB of memory at most, and
  the published copy mounted read-only. It has no `.env` and no API key, and
  no port on the Pi: it is on an internal network with no way out, which
  only `cloudflared` joins.
- `cloudflared` starts once `tunnel.env` holds the tunnel's token
  (`TUNNEL_TOKEN=…`). It is the only container of the site with a way out,
  on its own network (`172.30.250.0/24`). Until then the site runs, and
  nothing reaches it.
- A deploy restarts the site on the new image after the private reader
  passes its checks, and checks it the same way from inside its container
  (`/version` serving this commit on a fully migrated database, the
  Library). When the code brings a migration the published copy lacks, it
  first migrates a copy (`greek migrate`, with no network, as `greekpublic`)
  and swaps that in. Before the first publish there is nothing to start.
- Its logs show `cloudflared`'s address on the internal network, not
  visitors' addresses.

Publish (or publish again after new study data), from your computer:

```bash
ssh -t PI sudo /opt/ancient-greek-reader/app/deploy/publish.sh
```

[publish.sh](publish.sh) exports the live database in the private reader's
container (`greek export-data`, without the batches' bookkeeping, a few
minutes on a Pi 4), checks the export's checksum and the database it
decompresses to against its manifest, then checks that copy
(`integrity_check`, one file with no WAL, this code's last migration). It
stops the public web app, keeps the copy it replaces as
`greek.db.previous`, starts the app on the new one and checks it; if a
check fails, it puts the previous copy back. A deploy and a publish wait for
each other (`/run/lock/ancient-greek-reader.lock`). `publish.sh --restore`
puts the previous copy back.

```bash
sudo docker compose --env-file /opt/ancient-greek-reader/public/compose.env \
  -f /opt/ancient-greek-reader/app/deploy/public/docker-compose.yml ps   # or logs web
```

## Rollback

Revert the bad commit on `main` (`git revert`) and push; the deploy follows.
A reverted migration can't be undone that way: the app then refuses the
newer database. Put back the copy made before it
(`data/greek-backup-<date>-pre-<migration>.db`): stop the app, replace
`greek.db` (and remove `greek.db-wal` and `greek.db-shm`), and start it
again.

## Removing it

```bash
sudo docker compose --env-file /opt/ancient-greek-reader/public/compose.env \
  -f /opt/ancient-greek-reader/app/deploy/public/docker-compose.yml --profile tunnel down
sudo docker compose -f /opt/ancient-greek-reader/app/docker-compose.yml down --rmi all
sudo tailscale serve --https=8443 off
sudo rm -rf /opt/ancient-greek-reader /etc/sudoers.d/gha-deploy-ancient-greek-reader
sudo userdel -r greekreader
sudo userdel greekpublic
```

and remove the key's line from `/home/gha-deploy/.ssh/authorized_keys`.
Copy `data/greek.db` off the Pi first (`greek backup -`, above).
