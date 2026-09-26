#!/usr/bin/env python3
"""Compare latency, throughput and answers of two running `laya-serve` servers.

Typically the CPU image against a GPU one (see docs/docker.md). Start both, then:

    docker/benchmark.py --baseline http://127.0.0.1:8001 --candidate http://127.0.0.1:8000

LAYA_BASELINE_API_KEY / LAYA_CANDIDATE_API_KEY (or LAYA_API_KEY for both) set the bearer
tokens. Prints a Markdown report. Exits non-zero when the two servers disagree on a choice
or by more than --tolerance on any probability or score, so it doubles as a parity check.
Standard library only; run it on the host, not in the container.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUEST = json.loads((ROOT / "examples/docker/request.json").read_text(encoding="utf-8"))
NOUL = {"state": {"document": "I was charged twice. Please fix this ASAP."},
        "questions": {"billing": {"type": "noul", "instructions": "Is this ticket about billing?"}}}
PARAGRAPH = ("Customer writes that the subscription renewed on the wrong card, support closed the earlier "
             "ticket without a reply, the invoice shows a duplicate line item, and they want the money back "
             "before the weekend because rent is due. They also mention the mobile app logs them out after "
             "every update. ")
LONG = {**REQUEST, "state": {"document": PARAGRAPH * 30}}
WORKLOADS = {
    "1 `noul` question": NOUL,
    "`request.json` (3 questions)": REQUEST,
    "3 questions, ~1.5k-word document": LONG,
}


class Server:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.key = key

    def health(self):
        with urllib.request.urlopen(self.url + "/health", timeout=10) as r:
            return json.load(r)

    def post(self, body):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.url + "/v1/systemone", json.dumps(body).encode(), headers)
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=600) as r:
            out = json.load(r)
        return (time.perf_counter() - start) * 1000, out["answers"]


def latency(server, body, warmup, runs):
    for _ in range(warmup):
        server.post(body)
    times = sorted(server.post(body)[0] for _ in range(runs))
    return statistics.median(times), times[max(0, int(0.9 * runs) - 1)]


def throughput(server, body, clients, requests):
    start = time.perf_counter()
    with ThreadPoolExecutor(clients) as pool:
        list(pool.map(lambda _: server.post(body), range(requests)))
    return requests / (time.perf_counter() - start)


def values(answers):
    """Flatten answers to {name: number or label}, including per-label choice probabilities."""
    flat = {}
    for q, a in answers.items():
        for field in ("noul", "score", "choice"):
            if a.get(field) is not None:
                flat[q] = a[field]
        for label, p in (a.get("probabilities") or {}).items():
            flat[f"{q}[{label}]"] = p
    return flat


def compare(base, cand, tolerance):
    """Returns (largest numeric difference, list of disagreements)."""
    worst, problems = 0.0, []
    for k in sorted(set(base) | set(cand)):
        b, c = base.get(k), cand.get(k)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            worst = max(worst, abs(b - c))
            if abs(b - c) > tolerance:
                problems.append(f"{k}: {b} vs {c}")
        elif b != c:
            problems.append(f"{k}: {b!r} vs {c!r}")
    return worst, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", required=True, help="URL of the reference server, e.g. the CPU image")
    ap.add_argument("--candidate", required=True, help="URL of the server under test, e.g. the GPU image")
    ap.add_argument("--runs", type=int, default=30, help="timed requests per workload (default 30)")
    ap.add_argument("--warmup", type=int, default=5, help="untimed requests first (default 5)")
    ap.add_argument("--clients", type=int, default=8, help="concurrent clients for throughput (default 8)")
    ap.add_argument("--tolerance", type=float, default=0.02, help="allowed answer difference (default 0.02)")
    args = ap.parse_args()

    shared = os.environ.get("LAYA_API_KEY")
    servers = {
        "baseline": Server(args.baseline, os.environ.get("LAYA_BASELINE_API_KEY", shared)),
        "candidate": Server(args.candidate, os.environ.get("LAYA_CANDIDATE_API_KEY", shared)),
    }
    device = {name: s.health().get("device", "?") for name, s in servers.items()}
    b, c = f"baseline ({device['baseline']})", f"candidate ({device['candidate']})"

    print(f"{args.runs} timed requests per workload after {args.warmup} warm-up; latency in ms.\n")
    print(f"| workload | {b} p50 | p90 | {c} p50 | p90 | speedup |")
    print("|---|---:|---:|---:|---:|---:|")
    results, failures, worst = {}, [], 0.0
    for name, body in WORKLOADS.items():
        lat = {s: latency(srv, body, args.warmup, args.runs) for s, srv in servers.items()}
        results[name] = {s: values(srv.post(body)[1]) for s, srv in servers.items()}
        diff, problems = compare(results[name]["baseline"], results[name]["candidate"], args.tolerance)
        worst = max(worst, diff)
        failures += [f"{name}: {p}" for p in problems]
        lb, lc = lat["baseline"], lat["candidate"]
        print(f"| {name} | {lb[0]:.1f} | {lb[1]:.1f} | {lc[0]:.1f} | {lc[1]:.1f} | {lb[0] / lc[0]:.1f}x |")

    rates = {s: throughput(srv, REQUEST, args.clients, args.clients * 10) for s, srv in servers.items()}
    print(f"\nThroughput, {args.clients} concurrent clients, `request.json`: {b} {rates['baseline']:.1f} req/s, "
          f"{c} {rates['candidate']:.1f} req/s ({rates['candidate'] / rates['baseline']:.1f}x).\n")

    print(f"Answers, {b} / {c} (largest difference {worst:.4f}, tolerance {args.tolerance}):\n")
    for name, res in results.items():
        pairs = ", ".join(f"`{k}` {res['baseline'].get(k)} / {res['candidate'].get(k)}"
                          for k in res["baseline"] if "[" not in k)
        print(f"- {name}: {pairs}")

    if failures:
        print("\nFAIL: answers disagree beyond tolerance:\n" + "\n".join(f"- {f}" for f in failures), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
