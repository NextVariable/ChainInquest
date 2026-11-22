# ChainInquest closed beta deployment

Minimal single-host deployment: one Linux VPS, one Python process, systemd, and
Caddy terminating TLS. No containers, queues or managed services are required.
All paths below assume `/opt/chaininquest` and `/var/lib/chaininquest`; adjust
consistent with the unit file if you change them.

Nothing in this directory contains a real domain, token or API key.

## Layout

```text
/opt/chaininquest/
  releases/v4.1.0-beta.1/   checked-out tagged source
  app -> releases/v4.1.0-beta.1
/etc/chaininquest/beta.env  owner root:chaininquest, mode 0640
/var/lib/chaininquest/      owner chaininquest:chaininquest, mode 0700
/var/backups/chaininquest/  owner root, mode 0700
```

## Install

1. Create the service account and directories.

   ```sh
   sudo useradd --system --home /opt/chaininquest --shell /usr/sbin/nologin chaininquest
   sudo install -d -o chaininquest -g chaininquest -m 0700 /opt/chaininquest/releases
   sudo install -d -o chaininquest -g chaininquest -m 0700 /var/lib/chaininquest
   sudo install -d -o root -g root -m 0700 /var/backups/chaininquest
   sudo install -d -o root -g chaininquest -m 0750 /etc/chaininquest
   ```

2. Deploy the tagged release without touching `main`.

   ```sh
   sudo -u chaininquest git clone --branch v4.1.0-beta.1 --depth 1 \
     https://github.com/NextVariable/ChainInquest.git \
     /opt/chaininquest/releases/v4.1.0-beta.1
   sudo -u chaininquest python3 -m venv /opt/chaininquest/releases/v4.1.0-beta.1/.venv
   sudo ln -sfn /opt/chaininquest/releases/v4.1.0-beta.1 /opt/chaininquest/app
   ```

3. Issue the first token and write the environment file. Keep the token out of
   shell history and repository files.

   ```sh
   token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
   digest=$(printf %s "$token" | sha256sum | cut -d' ' -f1)
   sudo install -o root -g chaininquest -m 0640 \
     /opt/chaininquest/app/deploy/beta.env.example /etc/chaininquest/beta.env
   # Edit both values, then confirm. Deliver the token to the tester separately.
   sudo systemctl daemon-reload
   ```

4. Install Caddy, copy `deploy/Caddyfile` to `/etc/caddy/Caddyfile`, replace
   `{$BETA_DOMAIN}` with the approved hostname, and start Caddy.

5. Install the service and retention preview units.

   ```sh
   sudo install -m 0644 deploy/chaininquest.service deploy/chaininquest-retention.service \
     deploy/chaininquest-retention.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now chaininquest
   sudo systemctl enable --now chaininquest-retention.timer
   ```

The Python service listens only on `127.0.0.1:8000`. Caddy is the only public
listener. `ExecStartPre` runs `deploy/preflight.py`, so an invalid origin, token
map, permissions or root execution stops the unit before it serves traffic.

## Verify on the VPS

Run these after every deploy; all must pass.

```sh
systemctl is-active chaininquest
ss -ltnp | grep -E ':8000|:443'          # 8000 only on 127.0.0.1
curl -fsS http://127.0.0.1:8000/healthz  # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: wrong.example' \
  http://127.0.0.1:8000/healthz          # 403
curl -s -o /dev/null -w '%{http_code}\n' https://BETA_HOST/api/jobs/00000000000000000000000000000000
                                          # 401 without a token
curl -sI https://BETA_HOST | grep -i strict-transport-security
sudo journalctl -u chaininquest -n 50 --no-pager
```

- Confirm the exact HTTPS origin in `beta.env` matches the public hostname,
  including the port only if it is non-default.
- Confirm no `Authorization`, bearer token, digest or request body appears in
  `journalctl -u chaininquest` or Caddy logs.
- Confirm restart recovery: `sudo systemctl restart chaininquest`, then reload an
  existing job URL; an interrupted job shows its saved phase and can be resumed.
- Confirm exactly one service instance owns the store: the unit runs one
  process, and the per-job `.run.lock` rejects a second runner.

## Backup and restore

`deploy/backup.sh` stops the service, archives the store, writes a SHA-256 file,
and restarts the service. `deploy/restore.sh` verifies the checksum and archive
layout, moves the current store to `<store>.pre-restore-<stamp>` instead of
deleting it, restores, fixes ownership and starts the service.

```sh
sudo deploy/backup.sh
sudo deploy/restore.sh /var/backups/chaininquest/chaininquest-<stamp>.tar.gz
```

Run a restore rehearsal into a disposable directory before inviting testers.
Backups are not pruned automatically; remove old archives only after confirming
the newest restore works.

## Retention

The timer runs a dry run only. Review its output, then apply deliberately:

```sh
sudo journalctl -u chaininquest-retention -n 100 --no-pager
sudo -u chaininquest /opt/chaininquest/app/.venv/bin/python \
  /opt/chaininquest/app/scripts/beta_ops.py --store /var/lib/chaininquest --days 30
# After reviewing the candidate list:
sudo -u chaininquest /opt/chaininquest/app/.venv/bin/python \
  /opt/chaininquest/app/scripts/beta_ops.py --store /var/lib/chaininquest --days 30 --apply
```

The tool never deletes running, queued, locked, malformed, symlinked or
out-of-store entries, and re-checks status and the run lock immediately before
removal. Export feedback summaries before deleting aged jobs.

## Rollback

1. Stop the service: `sudo systemctl stop chaininquest`.
2. Back up the current store: `sudo deploy/backup.sh` (or a manual copy).
3. Point the symlink at the previous release:
   `sudo ln -sfn /opt/chaininquest/releases/<previous-tag> /opt/chaininquest/app`.
4. `sudo systemctl start chaininquest`, then repeat the verification commands.
5. If the store schema changed, restore the matching archive with
   `deploy/restore.sh`; keep the pre-restore copy until verification passes.

Do not move or edit the published `v4.1.0-beta.1` tag.

## Runbook

| Symptom | Check | Action |
| --- | --- | --- |
| Unit exits immediately | `journalctl -u chaininquest`; run `preflight.py` manually | Fix origin, token JSON, store mode or ownership |
| 502 from Caddy | `ss -ltnp`, healthz on loopback | Restart the unit; confirm it binds 127.0.0.1:8000 |
| 401 for a valid tester | Token delivered correctly; digest in env matches | Recompute digest; rotate rather than reuse |
| 403 `invalid_host` | Request Host vs `CHAININQUEST_BETA_ORIGIN` | Fix proxy Host preservation; do not relax the check |
| 403 `same_origin_required` | Browser Origin header | Use the exact HTTPS origin; never disable the check |
| 429 | Per-user GET/POST limits | Wait for the window; investigate abuse before raising limits |
| Investigation `partial`/`unknown` | Result gaps, provider status | Keep as unknown; do not present as zero activity |
| RPC 403 or index outage | Job gaps and `journalctl` | Retry later; a failed range must stay unknown |
| Disk full | `df -h`, store size | Stop accepting testers, back up, prune only after dry run |
| Corrupt `state.json` | Job returns `invalid_job` | Restore from backup; the request fails explicitly |
| Service killed mid-run | Job status and saved phase | Resume the job; the stale `.run.lock` is released on exit |

## Deployment checklist

Before starting the service:

- [ ] `v4.1.0-beta.1` is checked out at `/opt/chaininquest/releases/v4.1.0-beta.1`; the tag was not moved.
- [ ] Service account is non-root and owns `/var/lib/chaininquest` with mode 0700.
- [ ] `/etc/chaininquest/beta.env` is root:chaininquest 0640 and contains the exact HTTPS origin.
- [ ] Token digests are unique, lowercase SHA-256, one per invited tester.
- [ ] `preflight.py` exits 0 for the installed environment.
- [ ] Caddy has a certificate for the beta host and proxies only to 127.0.0.1:8000.
- [ ] Port 8000 is not reachable from the internet.
- [ ] A backup archive was created and a restore rehearsal succeeded.
- [ ] Retention dry run lists no running or fresh jobs.

## Post-deploy acceptance

- [ ] `/healthz` returns ok on loopback; Caddy returns 200 over HTTPS.
- [ ] An unauthenticated job request returns 401; a wrong Host returns 403.
- [ ] Two test accounts cannot read each other's job, result or feedback.
- [ ] Token rotation and revocation behave as documented.
- [ ] A cancelled or restarted job resumes from its saved phase.
- [ ] Unknown coverage is still displayed as unknown after restart.
- [ ] Logs contain no token, digest, Authorization header or request body.
- [ ] `systemctl restart chaininquest` recovers automatically and the unit stays active.
- [ ] Retention dry run shows the expected candidates and `--apply` is manual.
- [ ] Rollback to the previous release was rehearsed or documented with the exact commands.

## Cost

One 2 vCPU / 2 GB VPS (roughly USD 5–12 per month), one domain (roughly USD
10–15 per year), free TLS through Caddy, and optional free uptime monitoring.
No paid index or model API is required for the default deterministic mode.
