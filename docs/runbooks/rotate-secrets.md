# Rotating Home HQ credentials

Work in a private administrative terminal. Do not print environment files or put
credentials into shell arguments, chat, screenshots, issue bodies, or Git. Preserve
the unrelated service's configuration when rotating one integration.

## SimpleFIN

1. Disable the finance sync timer and stop any current sync service.
2. Revoke Home HQ's access in [SimpleFIN Bridge](https://bridge.simplefin.org/).
   A leaked read-only credential can still expose account information.
3. Create a new setup token after reviewing connected accounts. Use
   `scripts/setup_simplefin.py --output /home/homehq/data/.homehq-finance-next.env`.
   Enter the token at its hidden prompt; never re-use the old setup token.
4. Privately install the new 0600 file as `/home/homehq/data/.homehq-finance.env`, owned
   by homehq. The setup tool refuses overwrite; replacement is an explicit
   administrative action after successful claim.
5. Run one sync service, check generic status and balances after MFA, then re-enable
   the timer. The web service does not need the provider credential or a restart
   for this change. If provider identifiers change, compare scope and aliases
   rather than assuming newly identified rows are additional assets.

## MFA secret or recovery-code exposure

Use the enrollment CLI with the same exact username/slot and a fresh private
output directory. Explicitly confirm replacement. Install the resulting per-slot
environment file and restart Home HQ so the new secret is loaded. Re-enrollment
invalidates old recovery hashes and grants; test a new authenticator code and one
new recovery code before removing old private copies. Store remaining recovery
codes off the server. Never send the provisioning URI to a public QR generator.

## App session key/password exposure

Change the affected password hash using a hidden password prompt. Rotate the app's
SECRET_KEY as well when existing cookies may be stolen, then restart Home HQ.
Changing a password alone does not necessarily invalidate already-issued Flask
cookies. SECRET_KEY rotation logs everyone out; coordinate that short disruption.
Store the replacement only in the private main environment file.

## Anthropic and GitHub

Revoke the affected key in the provider's account console, create a replacement
with the same narrowly scoped purpose, update the appropriate private environment
entry, and restart Home HQ. For GitHub use a fine-grained token limited to this
repository and required contents permissions. Verify one ordinary operation and
inspect only redacted status output. Do not test by printing keys.

## Backup passphrase

Changing HOMEHQ_BACKUP_PASSPHRASE affects new backups only. Keep old passphrases
available for retained older backups, labeled by date. Test an off-box decrypt
with the new passphrase before retiring anything. After compromise, treat old
backups protected by an exposed passphrase as exposed too.
