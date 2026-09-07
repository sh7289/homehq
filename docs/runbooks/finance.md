# Finance: setup, deployment, and recovery

Finance is a read-only view of connected-account balances. It stores no
transactions and does not move money. Daily snapshots begin with the first sync;
the provider cannot reconstruct earlier balance history for this app. Totals are
separate per currency and only cover the accounts connected to this token.

## What the owner needs

1. A [SimpleFIN Bridge account](https://bridge.simplefin.org/), with the desired
   institutions connected and a setup token for this app. Check institution support
   before buying. On September 7, 2026 Bridge lists $1.50 plus tax monthly or $15
   plus tax annually; confirm current terms on its site. Signup/payment/bank consent
   happen directly with Bridge, never in Home HQ or agent chat.
2. A TOTP-compatible authenticator for each household member who wants Finance.
3. An administrative server session, a recent instance snapshot, and somewhere
   off the instance to keep encrypted backups and the recovery information.

The setup token is used once to obtain an access URL. Treat both as passwords.
Never paste them into chat, commit them, put them in command arguments, use a
public QR-code website, or include them in screenshots.
[Bridge developer guide](https://beta-bridge.simplefin.org/info/developers).

## Before live credentials

- Verify HTTPS and `HOMEHQ_BEHIND_TLS_PROXY=true`; gunicorn remains bound to
  localhost behind nginx. Finance is disabled by default.
- Follow [server hardening](server-hardening.md): confirm new key-only SSH sessions,
  browser-SSH allowance, security updates, and dependency audit. Browser SSH is
  still SSH and cannot bypass a broken daemon or all port-22 access being blocked.
- Verify `.homehq.env` and backup/MFA files are owned by `homehq`, mode 0600.
  Check metadata with `stat`, not secret contents. Avoid `systemctl show` environment
  dumps and broad `env`/`printenv` commands.
- Perform the encrypted backup restore drill and arrange off-instance delivery.
  An encrypted file on the same disk is only a local backup.

## Deploy code with Finance initially disabled

These are operator instructions, not actions already performed. Run privileged
commands as the administrative account, not inside a `homehq` login shell.
Integrate the reviewed finance branch with the UI branch before normal deployment.
Record the currently deployed commit so rollback has a known target.

As `homehq`, install the reviewed code and dependencies using a maintained Python
version supported by your Ubuntu installation (3.10 or newer for these pinned
packages; plan migration before that interpreter's support ends). The development
baseline used Python 3.9 and is not evidence of production-version validation.

```bash
sudo -iu homehq
cd /home/homehq/homehq
git status --short
git pull --ff-only
.venv/bin/python --version
.venv/bin/python -m pip install -r requirements.txt
exit
sudo install -d -m 700 -o homehq -g homehq /home/homehq/data
sudo install -d -m 700 -o homehq -g homehq /home/homehq/backups
```

Use a secure editor to update `/home/homehq/.homehq.env` with these nonsecret
settings. Replace any existing values rather than adding conflicting duplicates.
Do not source that environment file into a shell: systemd and shell quoting differ.

```ini
HOMEHQ_FINANCE_ENABLED=false
HOMEHQ_FINANCE_DB_PATH=/home/homehq/data/finance.db
HOMEHQ_FINANCE_ALIASES_FILE=/home/homehq/data/finance-aliases.json
```

The alias file is optional; omit its setting until you create it. It maps local
hashed account IDs to friendly labels. Never guess that a provider ID is a bank
account number. Keep aliases outside the repo with mode 0600.

## Enroll MFA before enabling Finance

Use each user's exact existing login name and their corresponding slot. The
enrollment DB must be the app's existing HOMEHQ_DB_PATH (the example below uses
the existing deployment's pantry.db). Enrollment adds MFA records without changing
pantry items. It does not create an app user or change their password.

```bash
sudo -u homehq /home/homehq/homehq/.venv/bin/python /home/homehq/homehq/scripts/enroll_totp.py --slot 1 --username YOUR_LOGIN_NAME --db /home/homehq/homehq/pantry.db --output-dir /home/homehq/enrollment-user1
sudo install -m 600 -o homehq -g homehq /home/homehq/enrollment-user1/totp.env /home/homehq/.homehq-mfa-user1.env
```

The new private directory contains `provisioning.txt` and `recovery-codes.txt`.
Open the provisioning file privately and enter the setup secret into the
authenticator (time-based, six digits). Store the recovery codes offline or in a
password manager. Repeat for slot 2 with a different output directory and
`.homehq-mfa-user2.env`. Keep enrollment files private and remove redundant copies
once authenticator and recovery access are verified. Do not share one user's secret
with the other. A fresh output directory is required for rotation.

Install the updated app service, which loads optional per-user MFA environment
files. It must NOT load `.homehq-finance.env`.

```bash
sudo install -m 644 /home/homehq/homehq/homehq.service /etc/systemd/system/homehq.service
sudo systemctl daemon-reload
sudo systemctl restart homehq
sudo systemctl status homehq --no-pager
```

For an MFA smoke test before real bank data, temporarily enable Finance with its
missing store: login, open Finance, verify the challenge, and see the setup-needed
state. Test each user and one recovery code; that code becomes consumed. Wait for
the next TOTP time step before reusing an authenticator code in another session.
Check `timedatectl status` if valid codes fail. Ordinary Pantry stays password-only.

## Claim the provider token once

After prerequisites are verified, create a token in Bridge for Home HQ. The
following command prompts without echo; it writes the access credential directly
to a new 0600 file and refuses to overwrite an existing file.

```bash
sudo -u homehq /home/homehq/homehq/.venv/bin/python /home/homehq/homehq/scripts/setup_simplefin.py --output /home/homehq/data/.homehq-finance.env
```

Do not run the claim twice or copy example `curl` commands containing credentials.
If a claim fails after the provider may have consumed the token, revoke it in
Bridge and create a new one. Validate the installed file's owner/mode, not its
contents in shared terminal output. The web app never needs this file in its
environment; only the scheduled sync does. Both services currently use the same
Unix account, so environment separation reduces accidental exposure, not the
impact of a compromised service account.

## Install sync and run the first refresh

```bash
sudo install -m 644 /home/homehq/homehq/deploy/homehq-finance-sync.service /etc/systemd/system/
sudo install -m 644 /home/homehq/homehq/deploy/homehq-finance-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start homehq-finance-sync.service
sudo systemctl status homehq-finance-sync.service --no-pager
sudo journalctl -u homehq-finance-sync.service -n 20 --no-pager
```

An inactive oneshot service after a successful run is normal; inspect its exit
status. The journal should contain generic outcomes only. A payment/auth error
requires action in Bridge; a partial refresh keeps available accounts and warns
about the rest. Don't repeatedly force refreshes to chase bank-side delays.

Enable Finance, restart Home HQ, and open `/finance` after MFA. Check every account
against its institution: connection scope, currency, provider date, and balance
sign, especially credit cards/loans. This app preserves provider signs; do not use
a total whose liability signs are wrong. Unconnected property, loans, and assets
are outside the total. No exchange rates are applied. Then enable the daily timer:

```bash
sudo systemctl enable --now homehq-finance-sync.timer
sudo systemctl list-timers homehq-finance-sync.timer --all
```

The schedule is 06:00 UTC with a small randomized delay. The UI reads cached
snapshots only. A successful network request does not prove each bank balance is
current; individual balance dates and incomplete states remain visible.

## Backup both databases and verify an off-box restore

The backup service reads HOMEHQ_DB_PATH and the optional HOMEHQ_FINANCE_DB_PATH
from main config, plus the passphrase from `.homehq-backup.env`. It does not read
the SimpleFIN credential file. Set `HOMEHQ_BACKUP_KEEP=14` to retain 14 backups
per database. Configuring a finance path before its first sync will make backup
report the missing source; create the finance store before enabling this schedule.

```bash
sudo install -m 644 /home/homehq/homehq/deploy/homehq-backup.service /etc/systemd/system/
sudo install -m 644 /home/homehq/homehq/deploy/homehq-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start homehq-backup.service
sudo systemctl enable --now homehq-backup.timer
```

Default off-box procedure is a pull to the owner's Mac, unless another destination
is already in place. Configure a dedicated SSH key and restricted read-only backup
access with the administrator; don't grant the app cloud credentials just to copy
backups. From a private local directory, pull only ciphertext with a known-working
SSH account able to read the backup directory. Replace the SSH alias below with
your configured backup host/account. A periodic launchd job can run the same rsync
command; choose a daily time after backups finish. A sleeping/offline Mac must catch
up later, so periodically check the newest local backup date.

```bash
umask 077
mkdir -p "$HOME/HomeHQ-backups"
rsync -av --include='*.db.gpg' --exclude='*' homehq-backup:/home/homehq/backups/ "$HOME/HomeHQ-backups/"
```

Do not use `--delete`: retain independent local recovery copies. The backup key's
server setup is site-specific and must be tested; the rsync alias is not installed
by this branch. Pulling requires a permitted SSH network path.

In a private local directory, decrypt one pantry and one finance backup with the
passphrase entered interactively. Use real downloaded filenames in these commands:

```bash
umask 077
gpg --output restored-finance.db --decrypt finance-DOWNLOADED_TIMESTAMP.db.gpg
sqlite3 restored-finance.db 'PRAGMA integrity_check;'
gpg --output restored-pantry.db --decrypt pantry-DOWNLOADED_TIMESTAMP.db.gpg
sqlite3 restored-pantry.db 'PRAGMA integrity_check; SELECT count(*) FROM pantry_items;'
```

Inspect the finance table schema and confirm expected account/snapshot row counts
privately; do not send balances to chat. Keep the backup passphrase and MFA recovery
information outside the instance. Restoring old pantry backups also restores older
MFA replay/recovery state: rotate/re-enroll MFA after a recovery to invalidate
previously consumed codes. A full recovery additionally needs app configuration,
catalog/recipe Git content, and any photos/uploads not backed by Git; DB backups
alone do not capture those files.

## Rollback and routine failures

To hide Finance, set HOMEHQ_FINANCE_ENABLED=false and restart the app. To stop data
collection, also `sudo systemctl disable --now homehq-finance-sync.timer` and stop
an active sync service. These are separate controls; hiding a page doesn't revoke
provider access. Preserve the DB/backups. Roll code back to the recorded good
commit using the deployment runbook after checking migrations. Revoke the Bridge
token if disconnecting permanently or if exposure is suspected.

If MFA is unavailable, the page fails closed; use recovery codes or administrative
re-enrollment. If the provider is unavailable, retain old balances with dates and
warnings. Missing config/store is setup-needed, never a fabricated zero total.
See [secret rotation](rotate-secrets.md). No server deployment, live token claim,
or institution verification is implied by passing local tests.
