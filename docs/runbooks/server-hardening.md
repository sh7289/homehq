# Server hardening (security plan, phase 2)

Server: AWS Lightsail, Ubuntu, static IP `3.18.204.81`.
All of this runs as `gridwatch` (full sudo). None of it touches app code.

**Keep a second SSH session open the whole time.** Every step below can lock
you out if it goes wrong, and an open session is the difference between
undoing a mistake in ten seconds and rebuilding from a snapshot. Do not close
it until the final verification passes from a *new* session.

Do these in order. The SSH changes are last on purpose — the recovery paths
come first.

---

## 1. Snapshot first

Lightsail console → Instance → Snapshots → **Create snapshot**. Wait for it
to finish. This is the undo button for everything below.

While you're there, enable **automatic snapshots** if you haven't (that is
also a backup-plan item).

---

## 2. Confirm you can get back in without SSH

Lightsail console → Instance → **Connect using SSH** (the browser terminal).
It connects through AWS rather than your own key, so it still works if you
break `sshd_config` or lock your IP out.

Confirm it opens and you can `sudo -n true`. If the browser console does not
work, **stop here** — everything below assumes it as the escape hatch.

---

## 3. Unattended security upgrades

Nothing currently patches this box.

```bash
sudo apt update
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades     # answer Yes
```

Confirm it will actually act, rather than only download:

```bash
sudo unattended-upgrades --dry-run --debug 2>&1 | tail -20
grep -r "Unattended-Upgrade::Allowed-Origins" -A6 /etc/apt/apt.conf.d/50unattended-upgrades
```

You want the `-security` origin uncommented. Reboots are **not** automatic by
default, which is the right call here — a reboot drops the app. Check for
pending ones now and then:

```bash
ls /var/run/reboot-required 2>/dev/null && echo "reboot needed"
```

---

## 4. fail2ban

```bash
sudo apt install -y fail2ban
sudo tee /etc/fail2ban/jail.local >/dev/null <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
EOF
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

The last command should report a jail with 0 currently banned. Come back in a
day and it will not be 0 — that is the point.

Note this covers SSH only. Home HQ's own login throttling (commit `ed85dd6`)
handles the web side, and it is per-username rather than per-IP on purpose.

---

## 5. SSH: keys only

Check you actually have a working key **before** disabling passwords:

```bash
grep -c . ~/.ssh/authorized_keys       # expect at least 1
```

Then:

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
EOF
sudo sshd -t && echo "config OK"
sudo systemctl reload ssh
```

**`sshd -t` must print "config OK" before you reload.** If it reports an
error, fix it before going further — reloading a broken config is how you
lose access.

Now, from a **new terminal**, open a fresh SSH session and confirm it works.
Only once that succeeds should you close the original.

---

## 6. Narrow port 22

Lightsail console → Instance → **Networking** → IPv4 Firewall.

The SSH rule defaults to `0.0.0.0/0`. Restrict it to your home address:

```bash
curl -s https://checkip.amazonaws.com     # your current public IP
```

Set the SSH rule's source to that address. **If your home IP is dynamic**
(most domestic connections are), it will change and lock you out — which is
survivable because of the browser console from step 2, but annoying. Two
sane options:

- Restrict to your ISP's rough range rather than a single address, or
- Close port 22 entirely and use the Lightsail browser console for all admin.

The second is the stronger position and costs you `scp`. Your call; either is
a large improvement on open-to-the-world.

Do **not** touch the 80 and 443 rules — the site needs them.

---

## 7. Dependency audit

```bash
sudo -iu homehq
cd ~/homehq && source .venv/bin/activate
pip install pip-audit
pip-audit -r requirements.txt
exit
```

Not automated on purpose: it needs a human to judge whether a finding matters.
Worth running when you deploy, or monthly.

---

## Verification

From a new session, with the original still open:

```bash
whoami                                  # gridwatch
sudo -n true && echo "sudo ok"
sudo fail2ban-client status sshd
systemctl is-enabled unattended-upgrades
sudo sshd -T | grep -E "^(passwordauthentication|permitrootlogin)"
```

Expect `passwordauthentication no` and `permitrootlogin no`.

Then from a phone on mobile data (a different network), confirm:

- `https://homehq.thenakedirish.com` still loads and you can log in.
- SSH to `3.18.204.81` times out, if you narrowed port 22.

Only then close the original session.

---

## If you lock yourself out

1. Lightsail browser console (step 2) — works even with port 22 closed.
2. If that fails too: Lightsail console → Snapshots → create a new instance
   from the snapshot taken in step 1, then re-attach the static IP.

The second path means a new instance, so the static IP has to be detached and
re-attached. That is the reason step 1 is not optional.
