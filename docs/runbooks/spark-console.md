# Spark console: the way back

Print this page and keep it next to the Spark. It is for the day the Spark's firewall locks you out, or a check fails. The Spark is built to **fail closed** ([spark-host.md](../architecture/spark-host.md)): when its rules break, it goes silent instead of open. Then only its own keyboard and screen reach it. Nothing here opens the Spark to the internet; if a step seems to need that, stop and ask.

**Status:** ready for arrival, not run yet; rehearsed once at arrival ([spark-host.md](spark-host.md), step 7). The boot-menu key in "The Spark does not start properly" is confirmed then.

**What you need:** your account name and its password (Proton Pass). The password works only here at the console and for `sudo`, never over SSH.

## SSH does not work
1. **Log in at the console** with your account and password.
2. **Ask the rules how they are:**

   ```bash
   sudo aetherspark-egress-rules check
   ```

   | It prints | Meaning | Next |
   |---|---|---|
   | `aetherspark-egress-rules: in place` | The firewall is fine | Step 5 |
   | lines ending in `rules not in place` | Something changed the rules | Step 3 |
   | `host mode is required but /etc/aetherspark/host-mode is missing` | The marker file was deleted | `sudo /opt/aetherspark/deploy/ansible/run.sh` puts it back; then step 2 again |

3. **Put the rules back:**

   ```bash
   sudo systemctl reload aetherspark-egress-rules
   sudo aetherspark-egress-rules check
   ```

   `in place`: try SSH again from the desktop. Still an error: step 4.
4. **The settings file.** An error naming a setting (`host mode needs AS_LAN_IF`, `not a single IPv4 address: …`) means `/etc/aetherspark/egress-rules.env` is wrong. Compare it with your copy in `ops.local/`:

   ```bash
   sudo cat /etc/aetherspark/egress-rules.env
   sudoedit /etc/aetherspark/egress-rules.env
   sudo systemctl reload aetherspark-egress-rules
   ```

   If the file looks right and it still fails, go back to the last release that worked. The checkout still has it, and this needs no internet:

   ```bash
   cd /opt/aetherspark
   sudo git tag --sort=-creatordate | head -n 5        # the newest tags
   sudo git checkout <the last tag that worked>
   sudo deploy/ansible/run.sh
   ```

   If `run.sh` stops with a download error, the two releases pin different Ansible versions: start the ops proxy first (`sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --wait egress-ops`), then run it again.

5. **The rules are fine, SSH still fails.** Usual causes, in order:
   - **the desktop's address changed** (the router reservation). The firewall refuses it before SSH sees it, so look in the firewall's drop log: `sudo journalctl -k -g aetherspark-host-drop -n 20` shows `SRC=` with the address it came from. If that is your desktop's new address, put it into both settings files (`AS_ADMIN_ADDR` in `/etc/aetherspark/egress-rules.env`, `spark_host_admin_addr` in `/etc/aetherspark/host.yml`, with `sudoedit`) and in `ops.local/site.md`, then run `sudo /opt/aetherspark/deploy/ansible/run.sh`;
   - the hardware key is not plugged in, or not touched in time; try the spare;
   - the Spark's own address changed: `ip -4 address show` against `ops.local/site.md`.

**Never run `aetherspark-egress-rules remove` on the Spark.** It is for tests: it takes the rules away and opens the host.

## A check failed (`systemctl --failed` shows `aetherspark-egress-check.service`)
```bash
sudo journalctl -u aetherspark-egress-check -n 30
```

The lines before `rules not in place` say what differs. `not the DROP policy with only the jump` means the rule tables were deleted or replaced. Put them back with step 3 above, then find what did it: `sudo journalctl --since "<time of the last good check>"` and look for a package install, a firewall tool or a reboot. Write down what you found; it is a finding for the design.

## The Spark does not start properly
1. Power it on and press **Esc** repeatedly until the boot menu appears *(key to confirm at the rehearsal)*.
2. Choose **Advanced options**, then the entry ending in **(recovery mode)**, then **root**: a root shell without network.
3. Read what went wrong: `journalctl -b -1 -p err` (the previous boot's errors).
4. `reboot` to try a normal start again. Do not delete or disable the firewall unit: it is what keeps the Spark closed.

## After any of this
Note in `ops.local/site.md` what happened, when, and what fixed it. If the cause is not clear, tell Claude in the next session: it becomes a finding for the design or the role.
