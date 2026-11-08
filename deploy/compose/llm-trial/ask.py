"""Talk to the trial's model: a chat that keeps the conversation, or one question with its speed.
Standard library only; runs inside the vllm container (docs/architecture/llm-trial.md):

    docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm python3 /trial/ask.py
        a chat; inside it: /new starts over, /think switches visible reasoning, /exit leaves
    docker compose -f deploy/compose/llm-trial/compose.yaml exec vllm \
        python3 /trial/ask.py "Your question" [--think] [--max-tokens N]
        one question, then its speed

The model itself remembers nothing: the chat keeps the conversation here, sends all of it with
every question, and forgets it when it ends. Nothing is written to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass

URL = "http://127.0.0.1:8000/v1/chat/completions"
HELP = "/new starts a new conversation, /think switches visible reasoning, /exit leaves"


@dataclass
class Reply:
    text: str  # the answer only, without the reasoning: what goes back into the conversation
    tokens: int
    seconds_to_first: float | None
    tokens_per_second: float | None


def ask(messages: list[dict[str, str]], think: bool, max_tokens: int) -> Reply:
    """Sends the conversation, prints the reply as it streams in, and returns it with its speed."""
    body = {
        "model": "interactive",
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": think},
    }
    request = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    start = time.monotonic()
    first: float | None = None
    tokens = 0
    answer: list[str] = []
    reasoning = False
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
                thought = delta.get("reasoning_content") or ""
                text = delta.get("content") or ""
                if (thought or text) and first is None:
                    first = time.monotonic()
                if thought:
                    if not reasoning:
                        sys.stdout.write("[reasoning] ")
                        reasoning = True
                    sys.stdout.write(thought)
                if text:
                    if reasoning:
                        sys.stdout.write("\n[answer] ")
                        reasoning = False
                    sys.stdout.write(text)
                    answer.append(text)
                sys.stdout.flush()
    end = time.monotonic()
    # No speed for a reply that arrived in one piece: there is no time span to divide by
    rate = None
    if first is not None and tokens > 1 and end > first:
        rate = (tokens - 1) / (end - first)
    return Reply("".join(answer), tokens, None if first is None else first - start, rate)


def speed(reply: Reply) -> str:
    if reply.seconds_to_first is None or reply.tokens_per_second is None:
        return f"--- {reply.tokens} tokens"
    return (
        f"--- {reply.tokens} tokens; first after {reply.seconds_to_first:.2f} s; "
        f"{reply.tokens_per_second:.1f} tokens/s after that"
    )


def chat(think: bool, max_tokens: int) -> int:
    messages: list[dict[str, str]] = []
    print(f"Chat with the trial's model. {HELP}.")
    while True:
        try:
            question = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question in ("/exit", "/quit"):
            return 0
        if question == "/new":
            messages.clear()
            print("(new conversation)")
            continue
        if question == "/think":
            think = not think
            print(f"(visible reasoning {'on' if think else 'off'})")
            continue
        if question.startswith("/"):
            print(f"({HELP})")
            continue
        messages.append({"role": "user", "content": question})
        sys.stdout.write("\nmodel> ")
        try:
            reply = ask(messages, think, max_tokens)
        except KeyboardInterrupt:
            messages.pop()
            print("\n(stopped; the question is not kept)")
            continue
        messages.append({"role": "assistant", "content": reply.text})
        print(f"\n{speed(reply)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="ask once and show the speed; none: a chat")
    parser.add_argument("--think", action="store_true", help="let the model reason visibly first")
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()
    if args.question is None:
        return chat(args.think, args.max_tokens)
    reply = ask([{"role": "user", "content": args.question}], args.think, args.max_tokens)
    print(f"\n\n{speed(reply)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
