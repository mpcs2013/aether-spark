"""Ask the trial's model one question and show the answer as it streams, then its speed.
Standard library only; runs inside the vllm container (docs/architecture/llm-trial.md):

    docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm \
        python3 /trial/ask.py "Your question" [--think] [--max-tokens N]

Without --think the model answers directly (Qwen3's thinking mode off).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/v1/chat/completions"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--think", action="store_true", help="let the model reason first")
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()
    body = {
        "model": "interactive",
        "messages": [{"role": "user", "content": args.question}],
        "max_tokens": args.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": args.think},
    }
    request = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    start = time.monotonic()
    first = None
    tokens = 0
    with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310 - fixed local URL
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                tokens = chunk["usage"]["completion_tokens"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                text = delta.get("reasoning_content") or delta.get("content") or ""
                if text and first is None:
                    first = time.monotonic()
                sys.stdout.write(text)
                sys.stdout.flush()
    end = time.monotonic()
    if first is not None and tokens > 1:
        print(
            f"\n\n--- {tokens} tokens; first after {first - start:.2f} s; "
            f"{(tokens - 1) / (end - first):.1f} tokens/s after that"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
