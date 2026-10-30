# Spark host (WP6.2)

How to set up the Spark's host after first boot, and how to run an ops session later. The design is [spark-host.md](../architecture/spark-host.md); this page is the order of work. Run it at the Spark's own keyboard and screen: the last step switches on the host firewall, and a mistake there must not cut off the session you are typing in. When something here goes wrong, [spark-console.md](spark-console.md) is the way back.

**Status:** ready for arrival, not run yet. Steps marked *(arrival)* read facts that are only known on the Spark; write each result into the Spark section of `ops.local/site.md`.

## Before the Spark arrives
- **Two hardware security keys** (FIDO2; two YubiKey 5 NFC, bought): the first for daily use, the second a spare kept apart from it, so a lost or broken key does not lock you out. On the desktop, in PowerShell, once per key, with only that key plugged in: `ssh-keygen -t ed25519-sk -O resident -O application=ssh:spark-key1 -C spark-key1 -f $HOME\.ssh\spark_key1_ed25519_sk` (then `key2` for the spare). It asks for the key's FIDO2 PIN; touch the key only when asked (a touch at any other time types a one-time password). **Resident** means the key lives on the YubiKey, protected by its PIN, so the YubiKey alone can restore the small `_sk` file if the desktop is lost (`ssh-keygen -K`; Windows' built-in OpenSSH cannot do that, Linux can: step 6). Each `.pub` file is a line the Spark will accept; the `_sk` file is useless without its key. Until step 6 proves the restore, the two `_sk` files go on the offline USB copies with the tag key (rule L-002). The register of keys and where the spare is kept are the home infrastructure's (a request to it). Done 2026-10-29.
- **AetherSpark's tag signing key** (done 2026-10-27). Release tags are signed with AetherSpark's own SSH key, `~/.ssh/aetherspark-tag-signing` (ED25519, passphrase in the password manager, registered with the home infrastructure under rule L-002); the first signed tag is `v0.1.0`. The repository's git settings sign tags with it (`gpg.format ssh`, `user.signingkey`, `tag.gpgSign`). Its `.pub` line goes on the Spark as the only allowed signer; the line for step 3 is the same one used to check a tag on the desktop: `<your git email> namespaces="git" <the .pub line>`.
- **The two settings files**, filled in from `ops.local/site.md` (never in the repository):
  - `host.yml`, from [deploy/ansible/host.example.yml](../../deploy/ansible/host.example.yml): your account name, both keys' `.pub` lines, the desktop's and the router's addresses, the public time servers' addresses. The LAN connection's name is read at arrival.
  - `egress-rules.env`, from [deploy/host/egress-rules.env.example](../../deploy/host/egress-rules.env.example): the router, the Home range, the NAS address and port, and the host-mode values (LAN interface, router, desktop, the public time servers' addresses: the two Cloudflare publishes for time.cloudflare.com).

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
| `systemctl show -p FragmentPath dgx-dashboard.service nvidia-dgx-telemetry.service` | Where NVIDIA's units live: one under `/etc/systemd/system` cannot be masked, the role then needs a change |
| `systemctl list-unit-files systemd-timesyncd.service` | That time comes from timesyncd (the role's time settings assume it) |

If any name differs from the role's defaults, change `host.yml` (or the defaults, in a pull request) before step 5.

## 3. Copy the settings
From the desktop (SSH still takes your password at this point, which is also your `sudo` password, so the Spark must be the real one):

```powershell
scp host.yml egress-rules.env <you>@<spark>:
```

The first connection shows the Spark's host key fingerprint and asks whether to trust it. **Compare it with the one you wrote down in step 2.** Only if they are the same, answer `yes`; if they differ, answer `no` and stop: something else answers at the Spark's address.

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
| `ssh -i $HOME\.ssh\spark_key1_ed25519_sk <you>@<spark>`, touch the key | You are in, with no question about the host key (it was trusted in step 3) |
| The same with the spare: `-i $HOME\.ssh\spark_key2_ed25519_sk`, the spare plugged in | You are in: the spare works before you need it |
| At the Spark's console, each YubiKey plugged into the Spark in turn: `cd $(mktemp -d) && ssh-keygen -K` (its PIN, touch), then `cat id_ed25519_sk_rk_spark-key*.pub` | A file `id_ed25519_sk_rk_spark-key1` (or `key2`) whose `.pub` line equals that key's line on the desktop: the YubiKey alone restores the key. Then delete the folder, and put the spare back where it is kept |
| `ssh -o PubkeyAuthentication=no <you>@<spark>` | `Permission denied (publickey)` |
| `ssh -i $HOME\.ssh\spark_key1_ed25519_sk -R 9999:localhost:22 <you>@<spark>` | `remote port forwarding failed` |

Then the design's tests S1–S12 and the egress tests E12, E14, E17 and E18, which we run together; the reboot test (S8) last: `sudo reboot`, log in again, run `check` again.

## 7. Rehearse the console runbook once
With [spark-console.md](spark-console.md) printed next to the Spark: follow its "SSH does not work" path once on purpose, while everything works, so the first time is not a real outage.

## The two hardware keys (rule L-002, point 8)
- **Where they live:** the daily key with you; the spare away from home, with the away backup disk, never with the printed recovery sheet. Their entries are in the home infrastructure's key register.
- **Quarterly, with the restore drill:** log in once with the spare (step 6's second row), then put it back. Tell the home infrastructure the date, for its register.
- **A key is lost or broken:** first take it off the Spark, then replace it. At the console: delete its line from `spark_host_admin_ssh_keys` in `/etc/aetherspark/host.yml` (`sudoedit`), then `sudo /opt/aetherspark/deploy/ansible/run.sh`; the role's check then accepts only the remaining key. A new key: `ssh-keygen -t ed25519-sk` on the desktop, add its `.pub` line the same way, run the role again, and tell the home infrastructure.

**Never add your account to the `docker` group**, even where NVIDIA's guides suggest it: membership is root without a password. Use `sudo docker`. The role's checks fail if anyone is in that group.

## Ops sessions (updates, about weekly)
The host reaches the internet only while the ops proxy runs, so every update starts and ends with it. Updates and the drift check work over SSH. **A full run of the role (`run.sh` without `--check`) happens at the console**: it can restart SSH and the network, and a run cut off halfway leaves the host closed.

```bash
cd /opt/aetherspark
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --wait egress-ops
sudo apt update && sudo apt upgrade                  # held packages (Docker, NVIDIA toolkit) stay
# firmware: the client downloads itself, and sudo drops proxy settings, so name the proxy here;
# install only in a planned session
sudo https_proxy=http://172.30.20.2:3128 fwupdmgr refresh && sudo fwupdmgr get-updates
sudo deploy/ansible/run.sh --check --diff            # drift: "changed=0 failed=0" means none
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops rm -sf egress-ops
```

Check mode means something only after the role has been applied once (step 5); before that it stops at files that do not exist yet. If check mode reports a change after an upgrade, a package switched something back on: run `sudo deploy/ansible/run.sh` once **at the console**, then check mode again.

**A new release tag:** with the ops proxy up, `sudo git -c http.proxy=http://172.30.20.2:3128 fetch --tags`, `sudo git checkout <new tag>`, then step 5, **at the console**.
