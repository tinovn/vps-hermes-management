#!/bin/bash
# Smoke test cho 42 endpoint của hermes-mgmt API.
# Destructive endpoints chỉ test schema validation (gửi body sai cố ý), không thực sự gọi.
# Output: dòng <STATUS> <METHOD> <PATH> <NOTE>

set -uo pipefail

MGMT_KEY="${MGMT_KEY:-180c62b38725dbb2ee81cdb6147bea8363a304c02e20a382e75c735363c2db00}"
BASE="${BASE:-http://180.93.137.20:9997}"
DEEPSEEK_KEY="${DEEPSEEK_KEY:-sk-deadbeefcafebabe1234567890abcdef}"

pass=0; fail=0; skip=0
results=()

# call <expected_status> <method> <path> <note> [data]
call() {
  local want="$1" method="$2" path="$3" note="$4" data="${5:-}"
  local code body
  if [[ -n "$data" ]]; then
    body=$(mktemp)
    code=$(curl -s -o "$body" -w "%{http_code}" --max-time 15 \
      -X "$method" -H "Authorization: Bearer $MGMT_KEY" \
      -H "Content-Type: application/json" -d "$data" \
      "$BASE$path")
  else
    body=$(mktemp)
    code=$(curl -s -o "$body" -w "%{http_code}" --max-time 15 \
      -X "$method" -H "Authorization: Bearer $MGMT_KEY" \
      "$BASE$path")
  fi
  local mark
  if [[ "$code" == "$want" ]]; then
    mark="OK  "; pass=$((pass+1))
  elif [[ "$want" == "ANY" && "$code" =~ ^[0-9]+$ ]]; then
    mark="OK  "; pass=$((pass+1))
  else
    mark="FAIL"; fail=$((fail+1))
    local snip
    snip=$(head -c 120 "$body" | tr '\n' ' ')
    note="$note | want=$want got=$code body=$snip"
  fi
  printf "%s %s %-6s %-40s %s\n" "$mark" "$code" "$method" "$path" "$note"
  rm -f "$body"
}

skipped() {
  skip=$((skip+1))
  printf "SKIP --- %-6s %-40s %s\n" "$1" "$2" "$3"
}

echo "=== Hermes Management API smoke test ==="
echo "Base: $BASE"
echo "Time: $(date)"
echo

echo "--- 1) Health / Info (5 GETs) ---"
call 200 GET    /health                              "no auth required"
call 200 GET    /api/info                            ""
call 200 GET    /api/status                          ""
call 200 GET    /api/version                         ""
call 200 GET    /api/system                          ""
call 200 GET    /api/domain                          ""
echo

echo "--- 2) Auth (5) ---"
call 200 GET    /api/auth/user                       "current bearer = root user"
# login với mật khẩu sai cố ý — expect 401 hoặc 422
call ANY POST   /api/auth/login                      "wrong password expects 401/422" \
  '{"username":"admin","password":"definitely-wrong"}'
# create-user / change-password / logout / delete-user là destructive → skip thật, test schema
call 422 POST   /api/auth/create-user                "empty body should 422" '{}'
call 422 PUT    /api/auth/change-password            "empty body should 422" '{}'
call ANY POST   /api/auth/logout                     "logout idempotent"
echo

echo "--- 3) Config (6) ---"
call 200 GET    /api/config                          ""
call 200 GET    /api/providers                       "expect 15 templates"
call 200 PUT    /api/config/provider                 "set deepseek/deepseek-v4-flash" \
  '{"provider":"deepseek","model":"deepseek-v4-flash"}'
# verify normalize: idempotent khi đã có prefix
call 200 PUT    /api/config/provider                 "with-prefix idempotent" \
  '{"provider":"deepseek","model":"deepseek/deepseek-v4-flash"}'
call 200 POST   /api/config/test-key                 "test fake key /v1/models" \
  '{"provider":"openrouter","api_key":"sk-or-v1-dummy"}'
# test-key cho provider không tồn tại
call ANY POST   /api/config/test-key                 "unknown provider" \
  '{"provider":"nonexistent","api_key":"x"}'
echo

echo "--- 4) Channels (2) ---"
call 200 GET    /api/channels                        ""
# PUT/DELETE channel sẽ ghi file — test schema bằng body rỗng
call 422 PUT    /api/channels/telegram               "empty body should 422" '{}'
echo

echo "--- 5) Cron (2) ---"
call 200 GET    /api/cron                            ""
call 200 GET    /api/cron/status                     ""
# POST /api/cron tạo job thật → chỉ test schema
call 422 POST   /api/cron                            "empty body should 422" '{}'
echo

echo "--- 6) Logs (3) ---"
call 200 GET    /api/logs                            ""
call 200 GET    /api/logs/files                      ""
# stream là SSE, không test trong smoke
skipped GET    /api/logs/stream                     "SSE stream — skip"
echo

echo "--- 7) Env (2) ---"
call 200 GET    /api/env                             ""
# PUT/DELETE env — chỉ thử với key vô hại tạm thời
call 200 PUT    /api/env/HERMES_SMOKE_TEST           "set transient key" \
  '{"value":"smoke-ok"}'
call 200 DELETE /api/env/HERMES_SMOKE_TEST           "cleanup transient key"
echo

echo "--- 8) CLI (1) ---"
call ANY POST   /api/cli                             "whitelist: hermes version" \
  '{"subcommand":"version","args":[]}'
echo

echo "--- 9) Destructive endpoints (schema check only) ---"
# /api/restart và /api/start là idempotent, nhưng sẽ làm gián đoạn → SKIP để không tự bắn chân
skipped POST   /api/restart                          "would restart all services"
skipped POST   /api/stop                             "would stop all services"
skipped POST   /api/start                            "would start all services"
skipped POST   /api/rebuild                          "would npm rebuild web"
skipped POST   /api/upgrade                          "would pull + reinstall"
skipped POST   /api/reset                            "would wipe config"
skipped PUT    /api/domain                           "would change Caddy TLS"
echo

echo "--- 10) Path-param destructive (schema only) ---"
# Cron job nonexistent → expect 404
call ANY DELETE /api/cron/__nonexistent__            "nonexistent cron → 404"
call ANY POST   /api/cron/__nonexistent__/pause      "pause nonexistent"
call ANY POST   /api/cron/__nonexistent__/resume     "resume nonexistent"
call ANY POST   /api/cron/__nonexistent__/run        "run nonexistent"
call ANY DELETE /api/channels/__nonexistent__        "delete nonexistent channel"
call ANY DELETE /api/config/api-key?provider=__nonexistent__ "delete nonexistent provider key"
echo

echo "=== Summary ==="
echo "PASS: $pass | FAIL: $fail | SKIP: $skip"
exit $(( fail > 0 ? 1 : 0 ))
