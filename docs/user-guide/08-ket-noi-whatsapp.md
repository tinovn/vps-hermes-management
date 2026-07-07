# 8. Kết nối WhatsApp

Cho phép bot trả lời tin nhắn qua **WhatsApp** (qua bridge Baileys tích hợp sẵn của
Hermes — mô phỏng phiên WhatsApp Web). Người dùng chỉ cần: **cài bridge (1 lần)** →
chọn chế độ → quét QR → bật. Không cần SSH, không cần cấu hình tay.

> **Khác Zalo ở đâu?** WhatsApp **không có khái niệm "owner/sếp"**. Chỉ 1 tài khoản
> WhatsApp (tài khoản bot) và một bước **bật (enable)** sau khi quét QR. Ai được phép
> nhắn bot thì khai qua `allowed_users` (theo số điện thoại).

---

## 8.1. Luồng kết nối (state machine)

WhatsApp cần **cài đặt bridge (Baileys) một lần** trước khi kết nối. Bridge KHÔNG
được cài sẵn lúc dựng VPS — dashboard cài theo yêu cầu qua nút **"Cài đặt WhatsApp
bridge"**. Chỉ khi `bridge_installed = true` mới cho quét QR.

```
                 POST /install (cài Baileys, ~2-4 phút)
not_installed ───────────────► installing ──(xong)──► disconnected
     ▲                            │  (lỗi)                  │
     │                            ▼                    POST /connect {mode}
     │                       install_failed                 ▼
     │                                              pending ──(quét QR)──► paired
     │                                                 │                     │
     │                                             GET /qr (PNG)   POST /enable {mode, allowed_users}
     └──────── POST /disconnect ◄──────── connected ◄──────────────────────┘
```

`GET /api/whatsapp/status` trả về `data.status` là một trong:

| status | Ý nghĩa | Dashboard nên hiện |
|--------|---------|--------------------|
| `not_installed` | Chưa cài bridge deps | Nút **"Cài đặt WhatsApp bridge"** |
| `installing` | Đang cài (npm) | Progress + log (`/install-status`) |
| `install_failed` | Cài lỗi lần trước | Thông báo lỗi + nút **"Thử lại"** |
| `disconnected` | Đã cài, chưa kết nối | Nút **"Kết nối WhatsApp"** |
| `pending` | Đang chờ quét QR | Ảnh QR + spinner "Đang chờ quét…" |
| `paired` | Đã quét xong, **chưa bật** | Form chọn mode + nút **"Bật"** |
| `connected` | Đang chạy | Badge xanh "Đang hoạt động" + nút "Ngắt" |

> **Vì sao có bước cài riêng?** Baileys là git-dependency phải build TypeScript
> lúc cài (~2-4 phút, ngốn >1GB RAM). Cài lúc dựng VPS sẽ làm chậm mọi bản cài kể
> cả khách không dùng WhatsApp — nên tách thành nút bấm. Endpoint `/install` tự lo
> luôn 2 thứ hay làm hỏng máy mới: rewrite git SSH→HTTPS (sub-dep libsignal-node
> trỏ `ssh://`) và tạo swap trên máy RAM thấp (tránh OOM khi build).

> Bridge WhatsApp không tự lộ QR ra HTTP — mgmt-api chạy một tiến trình phụ để bắt
> mã QR rồi render thành PNG. Chi tiết kỹ thuật ở `management-api/hermes_mgmt/routes/whatsapp.py`.

---

## 8.2. Xác thực (auth)

Mọi endpoint yêu cầu **1 trong 2**:

- Header `Authorization: Bearer <HERMES_MGMT_API_KEY>` (server-to-server), **hoặc**
- Cookie phiên đăng nhập dashboard (`POST /api/auth/login`) — trình duyệt tự gửi.

Trong dashboard (đã đăng nhập), gọi thẳng bằng cookie, **không cần** nhúng API key vào frontend.

---

## 8.3. Tham chiếu API

Base path: `/api/whatsapp`. Mọi response bọc trong envelope chung:

```json
{ "ok": true, "data": { ... }, "error": null }
```

### `GET /status`

Poll endpoint này (khuyên 2–3 giây/lần khi đang ở màn kết nối / cài đặt).

```jsonc
// 200 — ví dụ khi đang chạy
{
  "ok": true,
  "data": {
    "status": "connected",        // not_installed|installing|install_failed|disconnected|pending|paired|connected
    "bridge_installed": true,     // đã cài Baileys chưa (gate cho /connect)
    "install_state": "installed", // installed | installing | failed | not_installed
    "enabled": true,              // WHATSAPP_ENABLED trong .env
    "mode": "self-chat",          // bot | self-chat
    "allowed_users": "*",         // chuỗi đã lưu (số điện thoại, "," hoặc "*")
    "paired": true,               // đã có creds.json (đã quét QR) chưa
    "bridge_connected": true,     // bridge của gateway đang online không
    "pairing": null,              // trạng thái tiến trình quét QR: pending|connected|null
    "qr_ready": false,            // QR đã sẵn sàng để hiển thị chưa
    "valid_modes": ["bot", "self-chat"]
  },
  "error": null
}
```

### `POST /install`

Cài đặt WhatsApp bridge deps (Baileys) — nút **"Cài đặt WhatsApp bridge"**. Chạy
**job nền** (rewrite git SSH→HTTPS + tạo swap nếu RAM thấp + `npm install`, và
build trong cgroup riêng để không bị giới hạn RAM của mgmt), trả về **ngay**. Poll
`/install-status` để theo tiến độ. Idempotent (đang cài / đã cài → không chạy lại).

```jsonc
// 200
{ "ok": true, "data": { "install_state": "installing" }, "error": null }
// hoặc { "install_state": "installed" } nếu đã cài sẵn

// 503 — không tìm thấy thư mục bridge (hermes-agent chưa cài xong)
```

### `GET /install-status?log_lines=20`

Tiến độ cài + tail log (để hiện progress). `log_lines` ∈ `[0, 200]`.

```jsonc
{
  "ok": true,
  "data": {
    "install_state": "installing",  // installed | installing | failed | not_installed
    "installed": false,
    "log": ["...", "npm warn ...", "added 144 packages in 2m"]
  },
  "error": null
}
```

### `POST /connect`

Bắt đầu quét QR. Trả về **ngay**; QR sinh bất đồng bộ (poll `/status` + hiện `/qr`).
**Yêu cầu đã cài bridge** (`bridge_installed=true`) — nếu chưa sẽ trả 409.

```jsonc
// Request body (tùy chọn) — pre-lưu mode để /enable dùng lại mặc định
{ "mode": "self-chat" }           // hoặc "bot"; bỏ trống cũng được

// 200 — chưa pair → bắt đầu QR
{ "ok": true, "data": { "status": "pending", "qr_url": "/api/whatsapp/qr" }, "error": null }

// 200 — đã pair sẵn (bỏ qua QR, sang thẳng /enable)
{ "ok": true, "data": { "status": "paired", "qr_url": null }, "error": null }

// 400 — mode sai
// 409 — CHƯA cài bridge (bấm "Cài đặt WhatsApp bridge" trước) hoặc đang cài dở
// 503 — không spawn được tiến trình quét QR
```

### `GET /qr`

Trả **PNG thô** — gắn thẳng vào `<img>`. **Không** phải JSON envelope.

```html
<img src="/api/whatsapp/qr" alt="Quét bằng WhatsApp" />
```

- `200` + `image/png` — ảnh QR (header `Cache-Control: no-store`).
- `404` — QR **chưa sẵn sàng** (đợi 1–2s sau `/connect` rồi thử lại) **hoặc** đã quét xong.
- `503` — tiến trình quét QR chưa chạy (gọi `/connect` trước).

> Vì trình duyệt cache ảnh, nên thêm query đổi mỗi lần refresh: `/api/whatsapp/qr?t=${Date.now()}`.

### `POST /enable`

Bật WhatsApp: ghi cấu hình + restart gateway. **Yêu cầu đã pair** (`creds.json` tồn tại).

```jsonc
// Request body
{
  "mode": "bot",                 // bot | self-chat (bỏ trống → lấy mode đã lưu / "self-chat")
  "allowed_users": "84901234567" // BẮT BUỘC khi mode=bot: số có mã QG, cách nhau ","; hoặc "*" cho tất cả
}

// 200
{ "ok": true, "data": { "status": "enabled", "mode": "bot", "restarted": true }, "error": null }

// 409 — chưa quét QR (thiếu creds.json)
// 400 — mode sai, hoặc mode=bot mà thiếu allowed_users
```

### `POST /disconnect`

Đăng xuất + xoá phiên + `WHATSAPP_ENABLED=false` + restart gateway. Lần sau muốn dùng lại phải quét QR mới.

```jsonc
// 200
{ "ok": true, "data": { "status": "disconnected" }, "error": null }
```

### `GET /logs?lines=200`

Tail các dòng log liên quan WhatsApp (lọc từ log Hermes). `lines` ∈ `[10, 1000]`, mặc định 200.

```jsonc
{ "ok": true, "data": { "lines": ["[gateway.log] ...", "..."], "count": 42 }, "error": null }
```

---

## 8.4. Hai chế độ (mode)

| mode | Bot phản hồi ai | `allowed_users` |
|------|------------------|-----------------|
| **`self-chat`** | Chỉ tin nhắn **bạn tự gửi cho chính mình** (chat với "You") | Không cần |
| **`bot`** | Tin nhắn từ người khác | **Bắt buộc**: danh sách số (mã QG, không dấu `+`), cách nhau `,`; hoặc `"*"` = tất cả |

Ví dụ `allowed_users`: `84901234567,84987654321` (số Việt Nam bỏ số 0 đầu, thêm `84`).

---

## 8.5. Ví dụ tích hợp frontend

```js
const API = "/api/whatsapp";
// Nếu gọi server-to-server, thêm: { headers: { Authorization: `Bearer ${MGMT_KEY}` } }
const opts = { credentials: "include" };

async function getStatus() {
  const r = await fetch(`${API}/status`, opts);
  return (await r.json()).data;
}

// 0) Người dùng bấm "Cài đặt WhatsApp bridge" (chỉ 1 lần cho mỗi VPS)
async function installBridge() {
  await fetch(`${API}/install`, { ...opts, method: "POST" });
}

// Poll tiến độ cài; resolve khi installed, reject khi failed
function pollInstall(onProgress) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      const r = await fetch(`${API}/install-status?log_lines=10`, opts);
      const d = (await r.json()).data;
      onProgress?.(d);                    // hiện d.log để user thấy tiến độ
      if (d.install_state === "installed") { clearInterval(timer); resolve(); }
      if (d.install_state === "failed") { clearInterval(timer); reject(new Error("Cài thất bại")); }
    }, 3000);
  });
}

// 1) Người dùng bấm "Kết nối" → chọn mode (chỉ bật sau khi bridge_installed)
async function connect(mode) {
  const r = await fetch(`${API}/connect`, {
    ...opts,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  return (await r.json()).data; // { status: "pending" | "paired", ... }
}

// 2) Poll trạng thái + refresh ảnh QR tới khi paired/connected
function startPolling(onState) {
  const img = document.getElementById("wa-qr");
  const timer = setInterval(async () => {
    const s = await getStatus();
    onState(s);
    if (s.qr_ready) img.src = `${API}/qr?t=${Date.now()}`; // ép reload ảnh
    if (s.status === "paired" || s.status === "connected") clearInterval(timer);
  }, 2500);
  return () => clearInterval(timer);
}

// 3) Sau khi paired → bật
async function enable(mode, allowedUsers) {
  const r = await fetch(`${API}/enable`, {
    ...opts,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, allowed_users: allowedUsers }),
  });
  if (r.status === 409) throw new Error("Chưa quét QR");
  if (r.status === 400) throw new Error("Thiếu allowed_users cho mode=bot");
  return (await r.json()).data; // { status: "enabled", restarted: true }
}

async function disconnect() {
  await fetch(`${API}/disconnect`, { ...opts, method: "POST" });
}
```

### Gợi ý UX

1. `status = not_installed` → nút **"Cài đặt WhatsApp bridge"** → `installBridge()` +
   `pollInstall(onProgress)` (hiện log/progress ~2-4 phút). Chỉ làm **1 lần** cho mỗi VPS.
2. Cài xong (`status = disconnected`) → màn **chọn mode** (*Chỉ mình tôi* = self-chat /
   *Bot cho khách* = bot). Nếu `bot` → ô nhập số được phép (hoặc toggle "Tất cả" = `*`).
3. Bấm **Kết nối** → `connect(mode)` → hiện QR (`<img id="wa-qr">`) → `startPolling`.
4. `status` chuyển `paired` → hiện nút **Bật** → `enable(mode, allowedUsers)`.
5. `status` = `connected` → badge xanh "Đang hoạt động".

---

## 8.6. Lưu ý vận hành

- **Cài bridge chỉ 1 lần**: deps (Baileys) build TypeScript ~2-4 phút, chạy nền trong
  cgroup riêng nên không làm treo mgmt-api. Cài xong thì `bridge_installed` giữ `true`.
- **Phiên đăng nhập lưu trên đĩa VPS** — bot không bị đăng xuất khi restart dịch vụ.
- **Điện thoại phải online**: giống WhatsApp Web, máy chủ WhatsApp trên điện thoại phải bật net (Baileys là phiên linked-device).
- **Chỉ 1 phiên quét tại một thời điểm**: đừng gọi `/connect` song song nhiều tab. Nếu đang `connected` mà `/connect`, API trả `paired` chứ không tạo phiên mới.
- **Đổi số/đổi tài khoản**: gọi `/disconnect` rồi `/connect` lại để quét QR tài khoản khác.
- **Enable chỉ cần env**: `WHATSAPP_ENABLED=true` là gateway tự nhận (không đụng `config.yaml`). API tự ghi vào cả 2 store `.env` và restart gateway giúp.
- **Cài đặt tự lo prerequisites**: `/install` tự rewrite git SSH→HTTPS (sub-dep
  libsignal-node) và tạo swap 2GB nếu máy RAM thấp chưa có swap — không cần thao tác tay.

---

## 8.7. Xử lý sự cố nhanh

| Triệu chứng | Nguyên nhân / cách xử lý |
|-------------|--------------------------|
| `/install` xong nhưng `install_state=failed` | Xem `GET /install-status` (log). Thường do mạng lúc `npm`/git; bấm cài lại (`/install` idempotent) |
| `/connect` trả 409 | Chưa cài bridge (bấm **Cài đặt WhatsApp bridge**) hoặc đang cài dở — đợi `install_state=installed` |
| `GET /qr` trả 404 mãi | QR chưa sinh xong (đợi 1–2s) hoặc đã quét xong → poll `/status`, nếu `paired` thì chuyển bước bật |
| `/connect` trả 503 | Không spawn được tiến trình quét QR → kiểm tra Node + xem log mgmt-api |
| `/enable` trả 409 | Chưa quét QR — bắt người dùng quét trước |
| `/enable` trả 400 | `mode=bot` mà chưa nhập `allowed_users` (dùng `*` nếu muốn mở cho tất cả) |
| Bật xong `bridge_connected` vẫn `false` | Đợi ~10s cho gateway restart + bridge kết nối lại; xem `GET /logs` |
