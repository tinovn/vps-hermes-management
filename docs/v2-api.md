# Hermes VPS Management API — v2 Reference

Tài liệu này dành cho frontend của Service Panel (Tino) tích hợp với mgmt-api chạy trên VPS Hermes. Các endpoint v2 là **thin wrappers** quanh `hermes <subcommand>` — mỗi route gọi đúng 1 lệnh CLI tương ứng và trả về stdout/stderr/exit_code thô. Frontend chịu trách nhiệm parse stdout nếu cần.

> **v1 endpoints (`/api/config`, `/api/env`, ...) vẫn hoạt động** cho back-compat. Tất cả tính năng mới nên dùng v2.

---

## 1. Cơ chế chung

### Base URL

```
https://<vps-domain>/         (production, qua Caddy)
http://<vps-ip>:9997/          (direct, bỏ qua Caddy — dùng để debug)
```

Mọi endpoint dưới đây đều có prefix `/api/v2`.

### Authentication

Mọi request **phải** gửi 1 trong 2 hình thức xác thực:

| Cách | Header / Cookie | Khi nào dùng |
|---|---|---|
| Bearer token | `Authorization: Bearer <HERMES_MGMT_API_KEY>` | Server-to-server (Service Panel ↔ VPS) — **khuyến nghị** |
| Session cookie | `Cookie: session=<token>` | Sau khi user login qua `POST /api/auth/login` |

Lấy `HERMES_MGMT_API_KEY` trên VPS:
```bash
grep ^HERMES_MGMT_API_KEY /opt/hermes/.env | cut -d= -f2
```

Không hợp lệ → `401 Unauthorized` với body:
```json
{ "ok": false, "data": null, "error": "Not authenticated" }
```

### Response envelope

Tất cả response v2 có cùng shape:

```ts
interface ApiResponse<T = any> {
  ok: boolean;          // true nếu CLI exit 0; false nếu lỗi
  data: T | null;       // payload — xem chi tiết từng endpoint
  error: string | null; // mô tả lỗi (chỉ có khi ok=false)
}
```

Payload chuẩn cho v2 (gọi là `CliPayload`):

```ts
interface CliPayload {
  exit_code: number;    // 0 = success
  parsed: any | null;   // STRUCTURED JSON — đây là field FE nên dùng
  stdout: string;       // CLI stdout (ANSI-stripped) — để debug/fallback
  stderr: string;       // CLI stderr (ANSI-stripped)
  // + các field route-specific (ví dụ: provider, key, session_id, ...)
}
```

**Shape của `parsed` thay đổi theo từng endpoint** (xem cột "Parsed shape" trong mỗi namespace bên dưới). Khi endpoint chưa có parser bespoke, `parsed` là `list<string>` — mỗi dòng stdout đã strip trang trí.

Frontend rule: ưu tiên `data.parsed`, dùng `data.stdout` chỉ khi cần hiển thị nguyên bản trong dev/debug panel.

### HTTP status codes

| Status | Khi nào |
|---|---|
| `200` | CLI exit 0 (hoặc command như `doctor` / `check` cho phép exit ≠ 0) |
| `401` | Thiếu / sai auth |
| `422` | Body / param không hợp lệ (ví dụ provider chứa ký tự cấm) |
| `500` | CLI exit ≠ 0 (với commands không thuộc nhóm "tolerant") — detail có stderr |

### Cảnh báo về `stdout`

`hermes config show`, `hermes status`, `hermes sessions list`, v.v. trả output **dạng text trang trí ANSI**. Khi render ra UI cần:
1. Strip ANSI escapes (`/\x1b\[[0-9;]*m/g`)
2. Hoặc parse bằng regex tùy lệnh
3. Hoặc đơn giản hiển thị trong `<pre>` block

---

## 2. Namespace `config` — `/api/v2/config`

Wrap `hermes config <sub>`. Đây là core của agent settings (model, providers, terminal, browser, v.v.).

### GET `/api/v2/config/show`

Trả về full config đã parse thành nested dict theo từng section.

**Request:** không body.

**Response 200 — `data.parsed` shape:**
```ts
type ConfigShowParsed = Record<string, Record<string, any>>;
// section name → { key: value }
```

Ví dụ:
```json
{
  "ok": true,
  "data": {
    "exit_code": 0,
    "parsed": {
      "Paths": {
        "Config": "/opt/hermes/.hermes/config.yaml",
        "Secrets": "/opt/hermes/.hermes/.env",
        "Install": "/opt/hermes/hermes-agent"
      },
      "API Keys": {
        "OpenRouter": "(not set)",
        "Anthropic": "(set, sk-****abcd)",
        "OpenAI (STT/TTS)": "(not set)"
      },
      "Model": {
        "Model": { "default": "deepseek-chat", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1" },
        "Max turns": 90
      },
      "Display": { "Personality": "kawaii", "Reasoning": "off" }
    },
    "stdout": "◆ Paths\n  Config:       /opt/hermes/.hermes/...",
    "stderr": ""
  },
  "error": null
}
```

Lưu ý: parser tự nhận dạng giá trị Python literal (dict/list/bool/int) — `Model` field trong ví dụ trên là dict thật, không phải string.

### POST `/api/v2/config/set`

Set 1 key trong config.

**Request body:**
```json
{
  "key": "model.default",
  "value": "claude-sonnet-4-6"
}
```

| Field | Type | Constraint |
|---|---|---|
| `key` | string | match `^[A-Za-z_][A-Za-z0-9_.]*$` (dotted-path) |
| `value` | string | any |

**Response 200:**
```json
{
  "ok": true,
  "data": {
    "key": "model.default",
    "exit_code": 0,
    "stdout": "✓ Set model.default = claude-sonnet-4-6 in /opt/hermes/.hermes/config.yaml\n",
    "stderr": ""
  },
  "error": null
}
```

**Ví dụ keys thường dùng:**
- `model.default` — model id (`claude-sonnet-4-6`, `deepseek-chat`, ...)
- `model.provider` — provider id (`anthropic`, `deepseek`, ...)
- `model.base_url` — endpoint custom
- `terminal.backend` — `local` / `docker` / `ssh`
- `display.personality` — `kawaii` / `professional` / ...
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ... — provider API keys (lưu vào `.hermes/.env`)

### GET `/api/v2/config/path`

```json
{ "ok": true, "data": { "path": "/opt/hermes/.hermes/config.yaml" }, "error": null }
```

### GET `/api/v2/config/env-path`

```json
{ "ok": true, "data": { "path": "/opt/hermes/.hermes/.env" }, "error": null }
```

### POST `/api/v2/config/check`

Chạy `hermes config check` — phát hiện config drift. Exit non-zero **không trả 500**; surface qua `ok=false` + stdout.

### POST `/api/v2/config/migrate`

Chạy `hermes config migrate` để cập nhật config schema theo upstream mới.

---

## 3. Namespace `model` — `/api/v2/model`

### POST `/api/v2/model/switch`

Đổi model đang dùng (non-interactive form).

**Body:** `{ "model": "claude-sonnet-4-6" }`

---

## 4. Namespace `fallback` — `/api/v2/fallback`

Chain fallback provider khi model chính fail.

| Method | Path | Body | Mô tả |
|---|---|---|---|
| GET | `/api/v2/fallback` | — | List fallback chain |
| POST | `/api/v2/fallback` | `{provider, model?}` | Append vào chain |
| DELETE | `/api/v2/fallback/{provider}` | — | Remove provider khỏi chain |
| DELETE | `/api/v2/fallback` | — | Clear toàn bộ chain |

`provider` match `^[a-z0-9_-]+$`.

---

## 5. Namespace `auth` — `/api/v2/auth`

Quản lý credentials theo provider. **Đây là endpoint chính để Service Panel set API key cho user**.

### GET `/api/v2/auth` — list tất cả provider pools

### GET `/api/v2/auth/{provider}` — list pool của 1 provider

### POST `/api/v2/auth/{provider}/api-key` — add API key

**Body:** `{ "api_key": "sk-ant-..." }`

**Response 200:**
```json
{
  "ok": true,
  "data": {
    "provider": "anthropic",
    "exit_code": 0,
    "stdout": "✓ Added API key for anthropic\n",
    "stderr": ""
  },
  "error": null
}
```

### POST `/api/v2/auth/{provider}/oauth` — kick off OAuth flow

CLI sẽ in URL OAuth ra stdout. Frontend cần parse URL từ stdout và redirect user.

### DELETE `/api/v2/auth/{provider}/{index}` — remove credential ở index

`index` là số nguyên ≥ 0 (vị trí trong pool).

### POST `/api/v2/auth/{provider}/reset` — clear cooldowns
### GET `/api/v2/auth/{provider}/status` — show status
### POST `/api/v2/auth/{provider}/logout` — clear stored auth state

---

## 6. Namespace `sessions` — `/api/v2/sessions`

Lịch sử chat sessions.

| Method | Path | Body / Params | Mô tả |
|---|---|---|---|
| GET | `/api/v2/sessions` | — | List recent sessions |
| GET | `/api/v2/sessions/stats` | — | Stats (count, size, ...) |
| DELETE | `/api/v2/sessions/{session_id}` | — | Xóa 1 session |
| POST | `/api/v2/sessions/prune` | — | Xóa session cũ theo retention |
| POST | `/api/v2/sessions/{session_id}/rename` | `{title}` | Đổi tên |
| POST | `/api/v2/sessions/export` | `{output, session_id?}` | Export ra JSONL |

`output` phải nằm dưới `HERMES_HOME` (path traversal bị reject 422).

---

## 7. Namespace `memory` — `/api/v2/memory`

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/v2/memory/status` | Memory provider config hiện tại |
| POST | `/api/v2/memory/off` | Tắt external memory provider |

Để **bật/setup** memory: dùng `POST /api/v2/config/set` với `memory.*` keys (`memory.provider`, ...).

---

## 8. Namespace `skills` — `/api/v2/skills`

Quản lý skills (extensions). Hub-installed skills tới từ Hermes Skills Hub.

| Method | Path | Body / Params | Mô tả |
|---|---|---|---|
| GET | `/api/v2/skills` | — | List installed skills |
| POST | `/api/v2/skills/install` | `{identifier}` | Install từ hub / path / tap |
| DELETE | `/api/v2/skills/{name}` | — | Uninstall |
| POST | `/api/v2/skills/check` | — | Check upstream updates |
| POST | `/api/v2/skills/update` | — | Update all hub skills |
| POST | `/api/v2/skills/{name}/reset` | — | Un-stick bundled skill |
| GET | `/api/v2/skills/search?q=<query>` | — | Search hub |
| GET | `/api/v2/skills/inspect?identifier=<id>` | — | Preview skill |

`identifier` match `^[A-Za-z0-9_./@:-]{1,256}$` (cho phép format `hub/name`, `tap:name`, path).

---

## 9. Namespace `bundles` — `/api/v2/bundles`

Bundle nhóm skills.

| Method | Path | Body | Mô tả |
|---|---|---|---|
| GET | `/api/v2/bundles` | — | List bundles |
| POST | `/api/v2/bundles` | `{name, skills: []}` | Create |
| DELETE | `/api/v2/bundles/{name}` | — | Remove |
| POST | `/api/v2/bundles/reload` | — | Re-scan + report changes |

---

## 10. Namespace `tools` — `/api/v2/tools`

### GET `/api/v2/tools/summary`

Show enabled tools per platform. (Interactive `hermes tools` wizard không expose qua HTTP — dùng `POST /api/v2/config/set` cho tool-specific keys nếu cần.)

---

## 11. Namespace `webhook` — `/api/v2/webhook`

Webhook subscriptions cho event handling.

| Method | Path | Body | Mô tả |
|---|---|---|---|
| GET | `/api/v2/webhook` | — | List subscriptions |
| POST | `/api/v2/webhook` | `{name, prompt?, events: [], skills: []}` | Create subscription |
| DELETE | `/api/v2/webhook/{name}` | — | Remove |

---

## 12. Namespace `gateway` — `/api/v2/gateway`

Gateway = process kết nối Hermes với messaging platforms (Telegram, Discord, Slack).

> **Lưu ý:** trên VPS install, gateway chạy qua **systemd service** (`hermes-gateway.service`), không phải `hermes gateway start`. Để restart canonical hãy dùng `POST /api/restart` (v1) thay vì `/api/v2/gateway/restart` — endpoint v2 chỉ wrap CLI cho parity.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/v2/gateway` | List profiles + status |
| GET | `/api/v2/gateway/status` | Status detail |
| POST | `/api/v2/gateway/start` | Start (CLI's user-mode unit) |
| POST | `/api/v2/gateway/stop` | Stop |
| POST | `/api/v2/gateway/restart` | Restart |

---

## 13. Namespace `cron` — `/api/v2/cron`

Schedule jobs.

| Method | Path | Body / Params | Mô tả |
|---|---|---|---|
| GET | `/api/v2/cron` | — | List jobs |
| POST | `/api/v2/cron` | `{spec, prompt, name?}` | Create |
| PATCH | `/api/v2/cron/{job_id}` | `{spec?, prompt?, name?}` | Update (≥1 field) |
| POST | `/api/v2/cron/{job_id}/pause` | — | Pause |
| POST | `/api/v2/cron/{job_id}/resume` | — | Resume |
| DELETE | `/api/v2/cron/{job_id}` | — | Remove |

`spec` là cron expression (`0 * * * *`, `@daily`, ...).

**Ví dụ create:**
```json
{
  "spec": "0 9 * * 1-5",
  "prompt": "Summarize today's news",
  "name": "morning-briefing"
}
```

---

## 14. Namespace `kanban` — `/api/v2/kanban`

Task board built-in trong Hermes.

### Boards

| Method | Path | Body | Mô tả |
|---|---|---|---|
| POST | `/api/v2/kanban/init` | — | Tạo `kanban.db` nếu chưa có |
| POST | `/api/v2/kanban/boards` | `{slug}` | Create board |
| POST | `/api/v2/kanban/boards/{slug}/switch` | — | Set active board |
| POST | `/api/v2/kanban/boards/{slug}/rename` | `{name}` | Rename |
| DELETE | `/api/v2/kanban/boards/{slug}` | — | Archive/delete |

### Tasks (trên active board)

| Method | Path | Body | Mô tả |
|---|---|---|---|
| GET | `/api/v2/kanban/tasks` | — | List tasks |
| GET | `/api/v2/kanban/tasks/{task_id}` | — | Show task detail |
| POST | `/api/v2/kanban/tasks` | `{title, body?, assignee?, skill?}` | Create |
| POST | `/api/v2/kanban/tasks/{task_id}/assign` | `{profile}` | Assign |
| POST | `/api/v2/kanban/tasks/{task_id}/complete` | — | Mark done |
| POST | `/api/v2/kanban/tasks/{task_id}/block` | `{reason}` | Block |
| POST | `/api/v2/kanban/tasks/{task_id}/unblock` | — | Return to ready |

---

## 15. Namespace `curator` — `/api/v2/curator`

Skill maintenance bot.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/v2/curator/status` | Curator + skill stats |
| POST | `/api/v2/curator/run` | Trigger review (có thể chạy lâu, timeout 5min) |
| POST | `/api/v2/curator/backup` | Tar.gz snapshot |
| POST | `/api/v2/curator/rollback` | Restore from snapshot |
| POST | `/api/v2/curator/{skill}/pin` | Pin skill (không auto-transition) |
| POST | `/api/v2/curator/{skill}/unpin` | Unpin |
| POST | `/api/v2/curator/{skill}/archive` | Archive skill |

---

## 16. Namespace `profile` — `/api/v2/profile`

Multi-profile Hermes (mỗi profile có config + sessions riêng).

| Method | Path | Body | Mô tả |
|---|---|---|---|
| POST | `/api/v2/profile` | `{name, clone?}` | Create (optional clone từ profile khác) |
| DELETE | `/api/v2/profile/{name}` | — | Delete |
| POST | `/api/v2/profile/{name}/use` | — | Set sticky default |
| POST | `/api/v2/profile/{old}/rename` | `{new_name}` | Rename |

---

## 17. Namespace `backup` — `/api/v2/backup`

Backup full Hermes home + import.

### POST `/api/v2/backup`

**Body:** `{ "output": "backup-2026-05.zip", "quick": false }`

`output` resolve dưới `HERMES_HOME` (path traversal bị reject). Trả path đầy đủ trong stdout.

### POST `/api/v2/backup/import`

**Body:** `{ "zipfile": "backup-2026-05.zip" }`

`zipfile` phải tồn tại dưới `HERMES_HOME`. Sẽ overwrite state hiện tại — Service Panel nên hỏi confirm trước khi gọi.

### GET `/api/v2/checkpoints/status`

Show total size + project count của session checkpoints.

### POST `/api/v2/checkpoints/prune`

Cleanup checkpoints theo size cap.

---

## 18. Namespace `diagnostics` — `/api/v2/diagnostics`

Read-only diagnostic commands.

| Method | Path | Params | Mô tả |
|---|---|---|---|
| GET | `/api/v2/diagnostics/status` | `?all=true&deep=true` | `hermes status` |
| POST | `/api/v2/diagnostics/doctor` | `?fix=true` | `hermes doctor` (non-zero exit không trả 500) |
| GET | `/api/v2/diagnostics/dump` | `?show_keys=true` | Copy-pasteable setup summary |
| POST | `/api/v2/diagnostics/debug-share` | `?lines=500` | Upload debug report |
| GET | `/api/v2/diagnostics/insights` | `?days=7&source=anthropic` | Token / cost analytics |
| GET | `/api/v2/diagnostics/logs` | `?name=agent&lines=100` | View logs (replaces `/api/logs`) |

---

## 19. Common flows cho Service Panel

### Flow 1: Tạo VPS mới + setup provider cho khách hàng

```http
POST /api/v2/auth/anthropic/api-key
Authorization: Bearer ...
Content-Type: application/json

{ "api_key": "sk-ant-..." }
```

Rồi:

```http
POST /api/v2/config/set
{ "key": "model.default", "value": "claude-sonnet-4-6" }

POST /api/v2/config/set
{ "key": "model.provider", "value": "anthropic" }
```

Verify:
```http
GET /api/v2/diagnostics/status?deep=true
```

### Flow 2: Đổi sang DeepSeek

```http
POST /api/v2/auth/deepseek/api-key  { "api_key": "sk-..." }
POST /api/v2/config/set  { "key": "model.default", "value": "deepseek-chat" }
POST /api/v2/config/set  { "key": "model.provider", "value": "deepseek" }
POST /api/v2/config/set  { "key": "model.base_url", "value": "https://api.deepseek.com/v1" }
```

### Flow 3: Backup trước khi customer upgrade plan

```http
POST /api/v2/backup
{ "output": "pre-upgrade-2026-05-13.zip", "quick": true }
```

Path đầy đủ nằm trong response stdout. Tải file về qua SCP / endpoint upload riêng nếu cần.

### Flow 4: Setup messaging (Telegram)

```http
POST /api/v2/auth/telegram/api-key  { "api_key": "<BOT_TOKEN>" }
POST /api/v2/gateway/restart
```

### Flow 5: Schedule daily summary

```http
POST /api/v2/cron
{
  "spec": "0 9 * * *",
  "prompt": "Summarize yesterday's chat sessions",
  "name": "daily-summary"
}
```

---

## 20. Error handling — best practices

1. **Luôn check `ok`**, không chỉ HTTP status. CLI thành công nhưng có warning vẫn `ok=true`.
2. **Render `stdout` trong `<pre>` để giữ format**.  Hermes CLI dùng heavy Unicode + ANSI; strip ANSI nếu render plain text.
3. **Timeout phía client ≥ 60s** cho các lệnh dài (`skills install`, `curator run`, `backup`).
4. **Khi gọi `POST /api/v2/config/set` lên ANTHROPIC_API_KEY / OPENAI_API_KEY**, restart gateway+dashboard không tự động. Sau khi set xong gọi `POST /api/restart` (v1) để service load env mới.
5. **DELETE / destructive endpoints** → UI nên hỏi confirm.

---

## 21. Whitelist `POST /api/cli` (v1, optional escape hatch)

Nếu cần lệnh chưa có v2 endpoint, dùng generic passthrough:

```http
POST /api/cli
{ "subcommand": "version", "args": [] }
```

Whitelist: `version, status, doctor, dump, debug, insights, logs, config, model, fallback, auth, sessions, memory, checkpoints, skills, bundles, tools, gateway, webhook, whatsapp, cron, kanban, curator, profile, backup, import, lsp, pairing, setup`.

---

## 22. Bảng tham chiếu nhanh

| Service Panel feature | v2 endpoint |
|---|---|
| Bật/đổi provider | `POST /api/v2/auth/{provider}/api-key` + `POST /api/v2/config/set` (`model.default`, `model.provider`) |
| Xem trạng thái agent | `GET /api/v2/diagnostics/status?deep=true` |
| Debug khách hàng | `POST /api/v2/diagnostics/doctor` + `GET /api/v2/diagnostics/logs?lines=200` |
| Token usage report | `GET /api/v2/diagnostics/insights?days=30` |
| Backup định kỳ | `POST /api/v2/backup` (quick=false cho full) |
| Reset config (failsafe) | `POST /api/v2/backup/import` từ backup gần nhất |
| Customer self-service "delete data" | `POST /api/v2/sessions/prune` + (optional) `POST /api/v2/curator/backup` |
| Schedule auto-task | `POST /api/v2/cron` |
