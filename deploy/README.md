# Deploying to a Raspberry Pi

This is how the reader is deployed to a Raspberry Pi in Docker, reachable
only over [Tailscale](https://tailscale.com): GitHub Actions deploys every
push to `main` over the tailnet once the tests and the secret scan pass.
Beside it the same image can serve a public site on a published copy of the database
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
  repository variable `PI_DEPLOY_ENABLED` is `true`, the workflow joins the
  tailnet with [Tailscale's GitHub Action](https://tailscale.com/docs/integrations/github/github-action)
  as an ephemeral node tagged `tag:ci`, signed in with the run's GitHub
  identity token (workload identity federation: no Tailscale secret is
  stored), and removed when the job ends. The tailnet policy lets `tag:ci`
  reach the Pi's port 22 and nothing else, so the Pi needs no ssh port open
  to the internet. Over it the workflow logs in as `gha-deploy` and runs
  exactly
  `sudo -n /opt/ancient-greek-reader/app/deploy/deploy_pi.sh --non-interactive`.
  The login key is locked to that command in `authorized_keys`
  (`restrict,command="…"`), and sudo allows `gha-deploy` that command only.
  The script pulls, rebuilds, restarts the app, sets `tailscale serve`, and
  checks that `/version` shows this commit serving a fully migrated
  database, that the Library loads, and that the container runs the new
  image.
- Actions logs are public. With `--non-interactive` the script prints
  neither the tailnet's name nor the app's logs (they hold visitors'
  addresses); the secrets (the Pi's tailnet address among them) are masked.
  The Tailscale step runs quietly and pings nothing: a ping prints the
  address its connection goes to.
- A deploy that brings a new migration: the app copies the database into
  `data/` before applying it ([Database upgrades](../docs/cli.md#database-upgrades)).
  Those copies (about 200 MB each) stay until removed by hand.

## One-time setup

The Pi needs Docker with the compose plugin, git, curl, python3, nftables
(`nft`, for the public site's firewall) and Tailscale (up, without Tailscale SSH, so that port 22 on the tailnet is the
Pi's own sshd), and a login user `gha-deploy`
(`sudo useradd --create-home --shell /bin/bash gha-deploy`).
Location labels: `[LOCAL]` your computer, `[PI]` the Pi, `[GITHUB]`
github.com or `gh`, `[TAILSCALE]` the Tailscale admin console.

0. `[TAILSCALE]` In the tailnet policy (Access controls), make the tag
   and let it reach the Pi's ssh and nothing else. If the policy still lets everything reach everything
   (`"src": ["*"]`), narrow that rule to `autogroup:member` first: a tagged
   node is not a member, so `tag:ci` then gets only its own rule.
   ```jsonc
   "tagOwners": {"tag:ci": ["autogroup:admin"]},
   "grants": [
     {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},
     {"src": ["tag:ci"], "dst": ["<tailnet address>"], "ip": ["tcp:22"]},
   ],
   ```
   Then a trust credential (Settings, Trust credentials, Credential,
   OpenID Connect): issuer GitHub, scope `auth_keys` (write) with the tag
   `tag:ci`, and as its subject the one GitHub's tokens carry for a run on
   `main` (only such a run signs in): the repository's prefix, then
   `:ref:refs/heads/main`. The prefix is the `sub_claim_prefix` of
   `gh api repos/<owner>/<repository>/actions/oidc/customization/sub`; with
   GitHub's immutable subjects it holds the account's and the repository's
   numeric IDs, `repo:<owner>@<id>/<repository>@<id>`, so a different
   repository by the same name can't sign in. Keep the credential's client
   ID and audience for step 3.

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
   `gha-deploy`'s `authorized_keys`, locked to the deploy command, and
   deletes the copy in `/tmp`. A re-run (copy the key over again first)
   locks the key again instead of adding a copy.
3. `[GITHUB]` Set the secrets (`PI_HOST` is the Pi's tailnet address,
   `100.x.y.z`):
   ```bash
   gh secret set TS_OAUTH_CLIENT_ID --body <client ID>
   gh secret set TS_AUDIENCE --body <audience>
   gh secret set PI_HOST --body <tailnet address>
   gh secret set PI_SSH_KEY < $K/ancient-greek-reader-gha
   ssh-keyscan -t ed25519,ecdsa,rsa <tailnet address> | gh secret set PI_KNOWN_HOSTS
   shred -u $K/ancient-greek-reader-gha $K/ancient-greek-reader-gha.pub
   ```
   Before pinning, compare every host key type with the ones you already
   trust for the Pi (`ssh-keygen -lf`, or `ssh-keyscan` of its LAN
   address).
4. `[LOCAL]` The seed: a copy of your database made by
   [greek backup](../docs/cli.md#greek-backup) (the reader's own is not
   offered for download: [docs/data.md](../docs/data.md#getting-it)), and
   an `.env` with the API key if you will run `greek study` on the Pi:
   ```bash
   mkdir -p $K/greek-seed
   python greek.py backup $K/greek-seed/greek.db
   (umask 077; echo 'ANTHROPIC_API_KEY=…' > $K/ancient-greek-reader.env)
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
  can't log in); `greek.db.previous`, the copy the last swap replaced (a
  publish, a `--restore`, or a deploy that migrated the published copy);
  `visits/YYYY-MM-DD.json`, the site's visitor counts, a day's totals in each
  ([visits.py](../greek/web/visits.py); kept 400 days, readable by any user
  on the Pi, for a dashboard there);
  `releases/data-YYYY-MM-DD/`, the last three exports
  ([greek export-data](../docs/cli.md#greek-export-data)'s files); `incoming/`, where a new copy is checked (one that failed
  its checks is kept there as `greek.db.failed`); `compose.env`, the
  settings compose reads, which the scripts write; and from the tunnel's
  setup `tunnel.env` (its token, root's only), or `tunnel.env.off` while
  the tunnel is off ([The tunnel](#the-tunnel)).
- The web container runs as `greekpublic` with a read-only filesystem
  (`/tmp` in memory) but for `visits/`, no Linux capabilities, 512 MB of
  memory at most, and the published copy mounted read-only. It has no
  `.env` and no API key, and no port on the Pi: it is on an internal network
  with no way out (bridge `greek-site`), which only `cloudflared` joins.
- `cloudflared` starts once `tunnel.env` holds the tunnel's token
  (`TUNNEL_TOKEN=…`, [tunnel.sh](tunnel.sh)). It is the only container of
  the site with a way out, on its own network (bridge `greek-out`,
  `172.30.250.0/24`, no IPv6), which a firewall walls off from everything
  but the internet ([The tunnel](#the-tunnel)). Until then the site runs,
  and nothing reaches it.
- A deploy restarts the site on the new image after the private reader
  passes its checks, and checks it the same way from inside its container
  (`/version` serving this commit on a fully migrated database, the
  Library). When the code brings a migration the published copy lacks, it
  first migrates a copy (`greek migrate`, with no network, as `greekpublic`)
  and swaps that in. Before the first publish there is nothing to start.
- Its logs show `cloudflared`'s address on the internal network, not
  visitors' addresses.
- On its read-only database the app keeps each page it sends in memory,
  gzipped, and sends it again to the next request for it
  ([page_cache.py](../greek/web/page_cache.py)), up to 64 MB. A restart
  empties it, and every publish and deploy restarts the site; the outside
  check ([The tunnel](#the-tunnel), step 5) opens every page in the sitemap,
  which builds them into it again, so a reader's first visit to a book
  needn't wait the second or two a Pi takes to build one. It runs as soon
  as a deploy has finished; after a publish, fill it at once with
  `gh workflow run site-check.yml`. The
  private reader keeps nothing, so new study data shows there at once.

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
`greek.db.previous`, starts the app on the new one and checks it; if the
site then fails its checks, it puts the previous copy back. If the tunnel's
walls fail their check ([The tunnel](#the-tunnel)), it stops there, with the
new copy in place and the site stopped. A deploy, a publish and `tunnel.sh
on` or `off` wait for each other (`/run/lock/ancient-greek-reader.lock`), up
to 15 minutes. `publish.sh --restore` puts `greek.db.previous` back and keeps
the copy it replaces in its place; it refuses one that lacks this code's
migrations (publish again instead).

```bash
sudo docker compose --env-file /opt/ancient-greek-reader/public/compose.env \
  -f /opt/ancient-greek-reader/app/deploy/public/docker-compose.yml ps   # or logs web
```

### The tunnel

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
is the public site's way in: `cloudflared` connects out to Cloudflare, which
answers visitors at your hostname (TLS, caching static files) and passes
their requests through the tunnel to the web app. Nothing on the router
changes and the Pi opens no port.

**Its walls.** [public/firewall.nft](public/firewall.nft) drops whatever
comes in on the bridge `greek-out` to the Pi itself (any of its addresses:
its sshd and web servers listen on all of them), to private, tailnet,
link-local and reserved IPv4 ranges (the home network, the tailnet and
MagicDNS, Docker's other networks), and all IPv6, so `cloudflared` reaches
the internet and nothing else. Docker's internal network, which the web app
and `cloudflared` share, still reaches the Pi itself (its gateway address),
so the rules drop that too, by its bridge `greek-site`. The rules are an
nftables table of their own (`inet greek_public`): Docker, ufw and
Tailscale keep theirs in iptables' tables and never replace it, and a drop
in it is final whatever their chains accept. They are copied to `/etc/ancient-greek-reader/` and loaded
by `greek-public-firewall.service` at every boot, before Docker starts the
containers. If they fail to load at boot, the service loads
[public/firewall-closed.nft](public/firewall-closed.nft) instead, which cuts
both of the site's networks off from everything, the internet included, and
fails: Docker still starts (other apps need it), but `cloudflared` can't
connect, so the site is offline, and the outside check says so, until a
deploy, a publish or `tunnel.sh on` loads the rules. A deploy that brings
rules that fail to load leaves the loaded ones in place and stops.
`cloudflared` asks DNS servers on the internet (`1.1.1.1`), not the Pi's.

Whenever the site starts with a token (a deploy, a publish, `tunnel.sh
on`), the rules are loaded again and checked before `cloudflared` starts:
a throwaway container on both of its networks
([probe_walls.py](public/probe_walls.py)) tries the Pi's addresses (the
internal network's gateway among them), the home router (port 80), the
Pi's DNS servers on the home network (systemd-resolved's, or else those in
`/etc/resolv.conf`; the log says when it finds none) and the tailnet's,
which must not answer, and an address on the internet, which must. If one answers, `cloudflared` stays stopped and the
deploy fails. The log names what answered, never an address.

**Setting it up**, once (`[CLOUDFLARE]` is Cloudflare's dashboard, with
your domain on Cloudflare's DNS; its menus move now and then, so the names
below may differ):

1. `[CLOUDFLARE]` Zero Trust, Networks, Tunnels: create a tunnel
   (`cloudflared`), and give it a public hostname, `reader.example`, with
   the service `HTTP` `web:8999`. Keep its token: the last word of the
   install command it shows.
2. `[CLOUDFLARE]` Before anyone may see it, Zero Trust, Access,
   Applications: a self-hosted application for the hostname, with a policy
   that allows the email addresses of your test readers. For the outside
   check, a service token (Access, Service credentials) and a second policy
   on the application with the action Service Auth that allows it. Keep its
   client ID and secret.
3. `[CLOUDFLARE]` For the domain: SSL/TLS, Edge Certificates, Always Use
   HTTPS on; and the setting that blocks AI crawlers (Security, Bots).
   Leave Bot Fight Mode off: it would challenge the outside check.
4. `[PI]` After the first publish (`tunnel.sh` refuses before it), save the
   token and start the tunnel:
   ```bash
   ssh -t PI sudo /opt/ancient-greek-reader/app/deploy/tunnel.sh on
   ```
   It asks for the token (nothing shows as you paste), keeps it in
   `public/tunnel.env`, loads and checks the firewall, and starts
   `cloudflared`. `tunnel.sh status` shows its connections to Cloudflare.
   From then on deploys and publishes keep it running.
5. `[GITHUB]` The outside check
   ([site-check.yml](../.github/workflows/site-check.yml),
   [check_site.py](public/check_site.py)), after every deploy and hourly
   (GitHub starts a schedule when it can: every few hours in practice):
   `/version`, the Library, robots.txt and the sitemap, every page in it and
   each text's start (the Library's Start reading), with Interlinear off and
   on, and `http://` sent on to `https://`. After a restart this fills the page cache again (a few
   minutes for the Pi); other runs take a minute or two. Each check is tried
   three times, 20 seconds apart. GitHub
   emails a failed scheduled run to whoever last changed its schedule, and a
   failed run after a deploy to whoever set the deploy off.
   ```bash
   gh variable set PUBLIC_SITE_URL --body https://reader.example
   gh secret set CF_ACCESS_CLIENT_ID --body <client ID>
   gh secret set CF_ACCESS_CLIENT_SECRET   # paste the secret
   gh workflow run site-check.yml
   ```
   By hand from any computer: `CF_ACCESS_CLIENT_ID=… CF_ACCESS_CLIENT_SECRET=…
   python3 deploy/public/check_site.py https://reader.example`. Without
   Access (after the preview) it needs no token.

Take the site off the internet with `tunnel.sh off`: it stops
`cloudflared` and moves the token to `tunnel.env.off`, so no deploy or
publish starts it again; Cloudflare shows visitors its own error page.
`tunnel.sh on` starts it again with the same token, and `tunnel.sh on
--new-token` asks for a new one (after rotating it in Cloudflare).

## Rollback

Revert the bad commit on `main` (`git revert`) and push; the deploy follows.
A reverted migration can't be undone that way: the app then refuses the
newer database. Put back the copy made before it
(`data/greek-backup-<date>-pre-<migration>.db`): stop the app, replace
`greek.db` (and remove `greek.db-wal` and `greek.db-shm`), and start it
again. The public site's copy was migrated too, and a deploy stops at it
until it is put back: `publish.sh --restore` returns the copy from before
that deploy (its `greek.db.previous`), or publish again.

## Removing it

First copy `data/greek.db` off the Pi (`greek backup -`, above, while the
app still runs), and turn deploys and the outside check off:
`gh variable set PI_DEPLOY_ENABLED --body false` and `gh variable delete
PUBLIC_SITE_URL` (otherwise the check fails, and emails, every hour). Then
on the Pi (the firewall's three lines only if the tunnel was ever on):

```bash
sudo /opt/ancient-greek-reader/app/deploy/tunnel.sh off
sudo docker compose --env-file /opt/ancient-greek-reader/public/compose.env \
  -f /opt/ancient-greek-reader/app/deploy/public/docker-compose.yml down --rmi all
sudo systemctl disable --now greek-public-firewall.service
sudo rm -rf /etc/systemd/system/greek-public-firewall.service /etc/ancient-greek-reader
sudo nft delete table inet greek_public
sudo systemctl daemon-reload
sudo docker compose -f /opt/ancient-greek-reader/app/docker-compose.yml down --rmi all
sudo tailscale serve --https=8443 off
sudo rm -rf /opt/ancient-greek-reader /etc/sudoers.d/gha-deploy-ancient-greek-reader
sudo userdel -r greekreader
sudo userdel greekpublic
```

and remove the key's line from `/home/gha-deploy/.ssh/authorized_keys` (and
the `gha-deploy` user, if no other app deploys with it). Delete the
repository's deploy secrets (step 3, and step 5's), and in Tailscale the
trust credential and `tag:ci`'s grant. In Cloudflare, delete the tunnel and
its Access application.
