# Deploying Home HQ

Server: AWS Lightsail, static IP `3.18.204.81`, live at
https://homehq.thenakedirish.com

## The account rule (this has bitten twice)

Two accounts, two jobs. Mixing them up is the usual cause of a confusing
failure:

| Account | Use it for | Sudo |
|---|---|---|
| `gridwatch` | everything needing `sudo` — systemd, apt, /etc | full `(ALL : ALL) ALL` |
| `homehq` | `git pull` and anything inside `/home/homehq` | **none** (one scoped systemctl rule only) |

`sudo -iu homehq` opens a shell **as the service account**. Running `sudo`
inside that shell fails with "a password is required" and there is no
password that works. Always `exit` back to `gridwatch` first.

Note `sudo` caches credentials for ~15 minutes. If it stops prompting and
then suddenly asks again mid-sequence, that's the cache expiring, not a
new problem — you need the `gridwatch` password (keep it in the password
manager).

Also: a bare command without `sudo` cannot read `/home/homehq/.homehq.env`
(mode 600, owned by `homehq`). `grep: Permission denied` there is the
permissions working, not a fault.

## Standard deploy (code only)

```bash
# 1. Pull, as the service account
sudo -iu homehq
cd ~/homehq && git pull
exit                      # <- back to gridwatch before any sudo

# 2. Restart
sudo systemctl restart homehq
sudo systemctl status homehq --no-pager | head -5
```

Then load the site and click through the page you changed.

## When dependencies changed

`git pull` does **not** install anything. If `requirements.txt` changed:

```bash
sudo -iu homehq
cd ~/homehq && git pull
source .venv/bin/activate && pip install -r requirements.txt
exit
sudo systemctl restart homehq
```

`deploy.sh` in the repo root does pull + pip + restart in one go, run as
`homehq`.

## When env vars changed

New variables do not arrive with a `git pull` — `.env.example` is only
documentation. Add them from `gridwatch`:

```bash
echo 'HOMEHQ_SOMETHING=value' | sudo tee -a /home/homehq/.homehq.env
sudo grep HOMEHQ_SOMETHING /home/homehq/.homehq.env    # verify it stuck
sudo systemctl restart homehq
```

Use `tee`, not `nano` — an editor save has silently failed here before.
Single-quote any value containing `$` (bcrypt hashes especially); systemd
does not expand `EnvironmentFile` values, but a shell sourcing the file will.

## If it doesn't come back up

```bash
sudo journalctl -u homehq -n 50 --no-pager
```

Past causes, most common first:

- **`ModuleNotFoundError`** — dependency not installed; see above.
- **`KeyError: 'HOMEHQ_...'`** — a required env var is missing from
  `.homehq.env`. `create_app` reads several with `os.environ[...]` and dies
  at startup without them.
- **502 from nginx** — gunicorn isn't running; the journal says why.
- **403 on `/static`** — directory permissions:
  `sudo chmod o+x /home/homehq /home/homehq/homehq && sudo chmod -R o+rX /home/homehq/homehq/static`
- **Author identity unknown** on an approved catalog import — the `homehq`
  user needs `git config --global user.name` / `user.email`.
- **Large push fails (HTTP 400)** — `git config http.postBuffer 524288000`
  as the `homehq` user.

## Rollback

```bash
sudo -iu homehq
cd ~/homehq && git log --oneline -5      # find the last good SHA
git checkout <sha>
exit
sudo systemctl restart homehq
```

Get back onto the branch afterwards with `git checkout main`. Note database
migrations only ever add columns, so rolling code back is safe — an older
build ignores a column it doesn't know about.

## Pending deploy as of 2026-09-06

Six commits since `463f315` (the last one deployed): backups, pantry
sections, capture-by-description, the recipe database, the recipe bank
import, and AI ingredient extraction.

- **No new Python dependencies** — skip `pip install`.
- **One new env var**, optional but worth setting explicitly. It defaults to
  `<repo>/recipes`, so the app runs without it:
  ```bash
  echo 'HOMEHQ_RECIPES_DIR=/home/homehq/homehq/recipes' | sudo tee -a /home/homehq/.homehq.env
  ```
- **The backup timer is a separate one-off setup** — see
  `docs/plans/private/STATUS.md`. It needs `gnupg`, its own passphrase file,
  and the two unit files from `deploy/`. Not required for the app to run.

After restarting, check: the Pantry page groups into sections with a "sort
into sections" panel, and Recipes lists 20 imported recipes.
