# Spark host (WP6.2)

How to set up the Spark's host after first boot, and how to run an ops session later. The design is [spark-host.md](../architecture/spark-host.md); this page is the order of work. Run it at the Spark's own keyboard and screen: the last step switches on the host firewall, and a mistake there must not cut off the session you are typing in. When something here goes wrong, [spark-console.md](spark-console.md) is the way back.

**Status:** ready for arrival, not run yet. Steps marked *(arrival)* read facts that are only known on the Spark; write each result into the Spark section of `ops.local/site.md`.

## Before the Spark arrives
- **A hardware security key** (FIDO2, e.g. a YubiKey). On the desktop, in PowerShell: `ssh-keygen -t ed25519-sk -f $HOME\.ssh\spark_ed25519_sk`. Touch the key when it blinks. The `.pub` file is the line the Spark will accept; the other file is useless without the key itself.
- **Your git signing key.** Release tags are signed with the SSH key you already use to sign Ostium's checksums. Its `.pub` line goes on the Spark as the only allowed signer.
- **The two settings files**, filled in from `ops.local/site.md` (never in the repository):
  - `host.yml`, from [deploy/ansible/host.example.yml](../../deploy/ansible/host.example.yml): your account name, the hardware key's `.pub` line, the desktop's and the router's addresses. The LAN connection's name is read at arrival.
  - `egress-rules.env`, from [deploy/host/egress-rules.env.example](../../deploy/host/egress-rules.env.example): the router, the Home range, the NAS address and port, and the host-mode values (LAN interface, router, desktop).

## 1. First boot (WP6.1)
On the AetherSpark network: first boot needs the internet and installs the full software image; do not interrupt it. **Decline** the analytics and crash-reporting question. Then the OS and firmware updates, the disk layout and the Docker check of WP6.1.

## 2. Observe *(arrival)*
At the console, as your account. Write each answer down.

| Command | What to note |
|---|---|
| `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` | The host key's fingerprint, to compare at your first SSH login |
| `nmcli connection show` and `nmcli device` | The LAN connection's name (for `host.yml`) and the LAN interface (for `egress-rules.env`) |
| `resolvectl status` | That systemd-resolved is in use |
| `systemctl list-unit-files --state=enabled` and `systemctl list-timers` | Units not in the role's list that contact anything |
| `snap list` | The snaps to remove (`spark_host_snaps_removed`) |
| `lsmod \| grep -Ei 'wifi\|wlan\|bt\|mt7\|iwl\|80211'` | The Wi-Fi and Bluetooth drivers (`spark_host_radio_modules`) |
| `apt-cache policy` and `ls /etc/apt/sources.list.d` | Which sources serve installed packages (the ops allowlist) |
| `dpkg -l nvidia-dgx-telemetry cloud-init passim` | Which of them are installed |

If any name differs from the role's defaults, change `host.yml` (or the defaults, in a pull request) before step 5.

## 3. Copy the settings
From the desktop (SSH still takes your password at this point):

```powershell
scp host.yml egress-rules.env <you>@<spark>:
```

At the Spark's console:

```bash
sudo install -D -m 0600 -o root -g root ~/host.yml /etc/aetherspark/host.yml
sudo install -D -m 0600 -o root -g root ~/egress-rules.env /etc/aetherspark/egress-rules.env
echo '<your git email> namespaces="git" <your signing key .pub line>' | sudo tee /etc/aetherspark/allowed_signers
rm ~/host.yml ~/egress-rules.env
```

## 4. Bootstrap, at the console
The host still reaches the internet directly; after step 5 it reaches it only through the ops proxy.

```bash
# A root-owned checkout of a signed release tag
sudo git clone https://github.com/mpcs2013/aether-spark.git /opt/aetherspark
sudo git -C /opt/aetherspark checkout <tag>
sudo git -C /opt/aetherspark -c gpg.ssh.allowedSignersFile=/etc/aetherspark/allowed_signers verify-tag <tag>
# uv, from the same pinned image the dev container uses
id=$(sudo docker create ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1)
sudo docker cp "$id:/uv" /usr/local/bin/uv && sudo docker rm "$id"
# The egress proxies' image, and the ops proxy running (the role's packages come through it)
cd /opt/aetherspark
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --build --wait egress-ops
```

`verify-tag` must print `Good "git" signature for <your git email>`. Anything else: stop.

## 5. Apply the role, at the console
```bash
sudo /opt/aetherspark/deploy/ansible/run.sh
```

It ends with a summary line `localhost : ok=… changed=… failed=0`. **`failed=0` is what matters.** The last task switches on the host firewall. Run it a second time: now it must say `changed=0`. Then:

```bash
sudo aetherspark-egress-rules check
```

It must print `aetherspark-egress-rules: in place`.

## 6. Check from the desktop
| Do | Expect |
|---|---|
| `ssh -i $HOME\.ssh\spark_ed25519_sk <you>@<spark>`, touch the key | You are in. The first time it shows the host key's fingerprint: it must match step 2 |
| `ssh -o PubkeyAuthentication=no <you>@<spark>` | `Permission denied (publickey)` |
| `ssh -i $HOME\.ssh\spark_ed25519_sk -R 9999:localhost:22 <you>@<spark>` | `remote port forwarding failed` |

Then the design's tests S1–S12 and the egress tests E12, E14, E17 and E18, which we run together; the reboot test (S8) last: `sudo reboot`, log in again, run `check` again.

## 7. Rehearse the console runbook once
With [spark-console.md](spark-console.md) printed next to the Spark: follow its "SSH does not work" path once on purpose, while everything works, so the first time is not a real outage.

## Ops sessions (updates, about weekly)
The host reaches the internet only while the ops proxy runs, so every update starts and ends with it.

```bash
cd /opt/aetherspark
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --wait egress-ops
sudo apt update && sudo apt upgrade                  # held packages (Docker, NVIDIA toolkit) stay
sudo fwupdmgr refresh && sudo fwupdmgr get-updates   # firmware: install only in a planned session
sudo deploy/ansible/run.sh --check --diff            # drift: "changed=0 failed=0" means none
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops rm -sf egress-ops
```

If check mode reports a change after an upgrade, a package switched something back on: run `sudo deploy/ansible/run.sh` once, then check mode again.

**A new release tag:** with the ops proxy up, `sudo git -c http.proxy=http://172.30.20.2:3128 fetch --tags`, `sudo git checkout <new tag>`, then step 5.
