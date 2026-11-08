# LLM serving, first light — runbook

The order of work for the first model on the Spark. The design and its reasons are in [llm-trial.md](../architecture/llm-trial.md). Everything runs over SSH from the admin desktop; nothing here needs the console, since no step changes SSH or the host firewall.

**Before:** the release tag with this runbook is signed and pushed, and the home infrastructure has been told about the heavy job (A-012).

## 1. Log in and start an ops session
From the desktop (daily YubiKey plugged in):

```powershell
ssh -i $HOME\.ssh\spark_key1_ed25519_sk spark-admin@10.20.0.20
```

On the Spark: start the ops proxy, fetch the new tag through it, check its signature, then restart the proxy so it reads the new allowlist from that tag:

```bash
cd /opt/aetherspark
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --wait egress-ops
sudo git -c http.proxy=http://172.30.20.2:3128 fetch --tags
sudo git checkout <tag>
sudo git -c gpg.ssh.allowedSignersFile=/etc/aetherspark/allowed_signers verify-tag <tag>
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops up -d --wait --force-recreate egress-ops
```

`verify-tag` must print `Good "git" signature`. The host role is unchanged, so it does not need to run again.

## 2. The model store
Once per Spark: root's folder, readable by all, writable only by root:

```bash
sudo install -d -m 0755 -o root -g root /srv/models
```

## 3. Download the model and the engine (about 44 GB)
The model (32.5 GB), checked file by file. It runs in the background, so a lost SSH connection does not stop it:

```bash
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops --profile model-pull run -d --name model-pull model-pull qwen3-30b-a3b-fp8
sudo docker logs -f model-pull
```

`Ctrl+C` leaves the log; the download goes on. It ends with `17 files verified`. If it stops with an error, the same `run` command resumes where it stopped (first `sudo docker rm model-pull`).

At the same time, in a second SSH window, the engine image (11.2 GB), pulled by the Docker daemon through the ops proxy:

```bash
cd /opt/aetherspark && sudo docker compose -f deploy/compose/llm-trial/compose.yaml pull
```

When both are done, end the ops session: from now on nothing needs the internet.

```bash
sudo docker rm model-pull
sudo docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops rm -sf egress-ops
```

## 4. Start the model server
```bash
sudo docker compose -f deploy/compose/llm-trial/compose.yaml up -d --wait
```

Loading 31 GB of weights and preparing the GPU takes several minutes; `--wait` returns when the server answers its health check (at most 15 minutes). To watch: `sudo docker compose -f deploy/compose/llm-trial/compose.yaml logs -f vllm`.

## 5. Talk to it
A chat that keeps the conversation while it runs:

```bash
sudo docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm python3 /trial/ask.py
```

Type a question after `you>`; the answer streams in, with its speed below it. Inside the chat: `/new` starts a new conversation, `/think` switches the model's visible reasoning on or off, `/exit` (or `Ctrl+D`) leaves; `Ctrl+C` stops an answer that runs too long. The conversation is kept only while the chat runs, never on disk.

One question with its speed, for a quick check:

```bash
sudo docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm python3 /trial/ask.py "In three sentences: what does a mixture-of-experts model do?"
```

`--think` lets the model reason first (longer, shows its reasoning).

## 6. The measurement (NFR-PERF-01 and -02)
vLLM's own benchmark, one request at a time: an 8,000-token prompt and 256 tokens of answer, five times:

```bash
sudo docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm vllm bench serve --backend openai-chat --base-url http://127.0.0.1:8000 --endpoint /v1/chat/completions --model interactive --tokenizer /models/Qwen/Qwen3-30B-A3B-FP8/d206ba732169f29bb77fbf80fc2c4b81d4d30782 --dataset-name random --random-input-len 8000 --random-output-len 256 --num-prompts 5 --max-concurrency 1
```

Note from its summary: **Mean TTFT** (target at most 2,000 ms) and **Mean TPOT** (time per output token; tokens per second = 1000 ÷ TPOT in ms; target at least 40). In another window, the GPU's temperature and power while it runs:

```bash
nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used --format=csv -l 5
```

## 7. Stop
```bash
sudo docker compose -f deploy/compose/llm-trial/compose.yaml down
```

The model stays in `/srv/models` for WP5b.1. The numbers go into the design note.
