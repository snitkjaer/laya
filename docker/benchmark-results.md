# CPU vs ROCm benchmark: Radeon 8060S (gfx1151)

Output of [`benchmark.py`](benchmark.py), measured 2026-09-26. Rerun it to compare on your
own hardware; the numbers below are specific to this machine.

| | |
|---|---|
| Machine | AMD Ryzen AI Max+ 395 (16 cores / 32 threads) with Radeon 8060S (gfx1151) |
| Host | Fedora 44, kernel 7.2.7, Docker 29.8.1 |
| Laya | 0.3.20, `english` checkpoint revision `55cf4c4` |
| Baseline | CPU image (`TORCH_INDEX=cpu`, PyTorch 2.14.0+cpu), `LAYA_THREADS=16`, `OMP_NUM_THREADS=16` |
| Candidate | `compose.rocm.yaml` image (PyTorch 2.14.0+rocm7.2), `LAYA_DEVICE=cuda` |

Both servers ran at the same time on the same machine and shared the model cache volume.
The CPU server was built and started with:

```bash
docker build -t laya-cpu:bench --build-arg TORCH_INDEX=cpu .
docker run -d --name laya-cpu-bench -p 127.0.0.1:8001:8001 \
  -v laya2_model-cache:/home/laya/.cache/huggingface \
  -e LAYA_PORT=8001 -e LAYA_DEVICE=cpu -e LAYA_THREADS=16 -e OMP_NUM_THREADS=16 \
  --init laya-cpu:bench laya-serve
docker/benchmark.py --baseline http://127.0.0.1:8001 --candidate http://127.0.0.1:8000
```

A second run earlier the same day gave medians within 3% of these. One run while the
host was busy measured CPU `request.json` at 237 ms; GPU numbers did not move.

## Results

30 timed requests per workload after 5 warm-up; latency in ms.

| workload | baseline (cpu) p50 | p90 | candidate (cuda) p50 | p90 | speedup |
|---|---:|---:|---:|---:|---:|
| 1 `noul` question | 96.0 | 97.5 | 23.6 | 24.0 | 4.1x |
| `request.json` (3 questions) | 184.2 | 185.3 | 30.9 | 31.4 | 6.0x |
| 3 questions, ~1.5k-word document | 1085.0 | 1099.8 | 98.5 | 99.6 | 11.0x |

Throughput, 8 concurrent clients, `request.json`: baseline (cpu) 5.4 req/s, candidate (cuda) 32.2 req/s (6.0x).

Answers, baseline (cpu) / candidate (cuda) (largest difference 0.0086, tolerance 0.02):

- 1 `noul` question: `billing` 0.9458 / 0.9454
- `request.json` (3 questions): `department` billing / billing, `urgency` 1.0057 / 1.0101, `refund_requested` 0.8838 / 0.8846
- 3 questions, ~1.5k-word document: `department` billing / billing, `urgency` 1.1599 / 1.1513, `refund_requested` 0.6901 / 0.6916
