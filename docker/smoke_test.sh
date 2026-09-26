#!/usr/bin/env bash
# Curl smoke test for a running `laya-serve` (see compose.http.yaml / docs/docker.md).
#
#   docker compose -f compose.yaml -f compose.http.yaml -f compose.rocm.yaml up -d --build --wait laya-serve
#   docker/smoke_test.sh                      # or LAYA_URL=http://host:8000 docker/smoke_test.sh
#
# Checks /health, the model-card example (https://huggingface.co/convaiinnovations/laya),
# the bundled examples/docker/request.json, and that bad input is rejected with 4xx.
# EXPECT_DEVICE (e.g. "cuda" for ROCm/CUDA) additionally asserts the server's device.
# Needs curl and jq. Exits non-zero on the first failure.
set -euo pipefail

URL="${LAYA_URL:-http://localhost:${LAYA_PORT:-8000}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTH=()
[[ -n "${LAYA_API_KEY:-}" ]] && AUTH=(-H "Authorization: Bearer ${LAYA_API_KEY}")

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok   $*"; }

# POST a body to /v1/systemone; prints the response body, sets $STATUS.
post() {
    local out
    out="$(curl -sS -w '\n%{http_code}' --max-time "${LAYA_TIMEOUT:-600}" "${AUTH[@]}" \
        -H 'Content-Type: application/json' "$URL/v1/systemone" --data "$1")"
    STATUS="${out##*$'\n'}"
    BODY="${out%$'\n'*}"
}

# jq assertion against $BODY.
check() {
    local desc="$1" expr="$2"
    jq -e "$expr" >/dev/null <<<"$BODY" || { echo "$BODY" | jq . >&2; fail "$desc"; }
    pass "$desc"
}

# 1. health
BODY="$(curl -sS --max-time 10 "$URL/health")" || fail "GET $URL/health unreachable"
check "/health status ok" '.status == "ok"'
if [[ -n "${EXPECT_DEVICE:-}" ]]; then
    check "/health device is $EXPECT_DEVICE" ".device == \"$EXPECT_DEVICE\""
fi
echo "     health: $(jq -c . <<<"$BODY")"

# 2. model-card example (noul)
post '{
  "state": {"document": "I was charged twice. Please fix this ASAP."},
  "questions": {"billing": {"type": "noul", "instructions": "Is this ticket about billing?"}}
}'
[[ "$STATUS" == 200 ]] || fail "model-card example: HTTP $STATUS: $BODY"
check "model-card: billing is noul" '.answers.billing.type == "noul"'
check "model-card: billing noul > 0.5" '.answers.billing.noul > 0.5'
check "model-card: usage present" '.usage | type == "object"'
echo "     billing.noul=$(jq .answers.billing.noul <<<"$BODY") model=$(jq -r .model <<<"$BODY")"

# 3. bundled docker request (choice + score + noul)
post "@$ROOT/examples/docker/request.json"
[[ "$STATUS" == 200 ]] || fail "examples/docker/request.json: HTTP $STATUS: $BODY"
check "request.json: department routes to billing" '.answers.department.choice == "billing"'
check "request.json: probabilities sum to ~1" \
    '(.answers.department.probabilities | add) as $s | $s > 0.99 and $s < 1.01'
check "request.json: urgency score within legend" \
    '.answers.urgency.score >= 0 and .answers.urgency.score <= 2'
check "request.json: refund_requested noul > 0.5" '.answers.refund_requested.noul > 0.5'
echo "     department=$(jq -r .answers.department.choice <<<"$BODY")" \
     "urgency=$(jq .answers.urgency.score <<<"$BODY")" \
     "refund=$(jq .answers.refund_requested.noul <<<"$BODY")"

# 4. bad input is rejected, not a 500
post '{"state": {"a": 1}}'
[[ "$STATUS" == 400 ]] || fail "missing questions: expected 400, got $STATUS"
pass "missing 'questions' -> 400"
post '{"state": {"a": 1}, "questions": {"q": {"type": "choice", "instructions": "x"}}}'
[[ "$STATUS" == 422 ]] || fail "choice without criteria: expected 422, got $STATUS: $BODY"
pass "choice without criteria -> 422"

echo "all checks passed against $URL"
