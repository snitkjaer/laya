# Testing `laya-serve` with curl

How to start the HTTP server on the ROCm host and call it with curl, locally or from another
machine on the LAN. For the full Docker guide see [docs/docker.md](../docs/docker.md).

## 1. Start the server

Local only (published on `127.0.0.1:8000`, no auth):

```bash
docker compose -f compose.yaml -f compose.http.yaml -f compose.rocm.yaml up -d --build --wait laya-serve
```

Reachable from other machines: publish on all interfaces and require an API key, since the
API is unauthenticated otherwise.

```bash
export LAYA_API_KEY=$(openssl rand -hex 16); echo "$LAYA_API_KEY"
LAYA_BIND_ADDRESS=0.0.0.0 docker compose -f compose.yaml -f compose.http.yaml -f compose.rocm.yaml \
    up -d --wait laya-serve
```

Drop `--build` when the image is already built. The first request per checkpoint downloads
and loads the weights, so it can take a while; later requests are fast.

Find the host address to use from the other machine with `ip -4 -br addr`. The Fedora
Workstation firewall zone already allows TCP 1025-65535, so port 8000 needs no extra rule.

Stop it with the same files: `docker compose -f compose.yaml -f compose.http.yaml -f compose.rocm.yaml down`.

## 2. Call it

Set these on the client machine (use `localhost` on the host itself):

```bash
HOST=http://<host-ip>:8000
KEY=<the LAYA_API_KEY value>
```

Health (never needs the key). `"device": "cuda"` means it is running on the GPU via ROCm:

```bash
curl -s $HOST/health
# {"status":"ok","loaded":[],"revisions":{},"device":"cuda"}
```

A yes/no (`noul`) question:

```bash
curl -s $HOST/v1/systemone \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  --data '{
    "state": {"document": "I was charged twice. Please fix this ASAP."},
    "questions": {"billing": {"type": "noul", "instructions": "Is this ticket about billing?"}}
  }'
# .answers.billing.noul ~ 0.95
```

`choice`, `score` and `noul` together, using the bundled request (run from the repo root, or
copy [examples/docker/request.json](../examples/docker/request.json) over):

```bash
curl -s $HOST/v1/systemone \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  --data @examples/docker/request.json
# department -> "billing", urgency score ~ 1.0 (0-2 scale), refund_requested noul ~ 0.88
```

Pipe through `| jq .` for readable output. Without `LAYA_API_KEY` set on the server, leave out
the `Authorization` header.

Question types:

| type | needs `criteria` | answer field |
|---|---|---|
| `noul` | no | `noul`: probability of yes, 0-1 |
| `choice` | yes, `{"label": "description", ...}` | `choice` plus `probabilities` per label |
| `score` | yes, an ordered list of levels | `score`: position on the list, 0 to len-1 |

Bad input returns 4xx, not 500: a body without `questions` gets 400, a `choice` without
`criteria` gets 422.

## 3. Run the full smoke test

[smoke_test.sh](smoke_test.sh) runs all of the above as assertions (needs curl and jq):

```bash
docker/smoke_test.sh                                                   # on the host
LAYA_URL=http://<host-ip>:8000 LAYA_API_KEY=<key> EXPECT_DEVICE=cuda docker/smoke_test.sh   # from another machine
```

It prints `all checks passed against ...` on success and exits non-zero on the first failure.
