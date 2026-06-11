# 6. Vận hành & giám sát

Toàn bộ thao tác vận hành thực hiện được từ dashboard/API — không cần SSH.

## 6.1. Kiểm tra trạng thái

| Thao tác | API | Trả về |
|----------|-----|--------|
| Health check (không cần auth) | `GET /health` | Hệ thống còn sống |
| Trạng thái 3 service | `GET /api/status` | `hermes-gateway / hermes-dashboard / hermes-mgmt` active hay không |
| Thông tin chung | `GET /api/info` | URL dashboard, domain |
| Phiên bản | `GET /api/version` | Phiên bản Hermes + mgmt-api |
| Tài nguyên máy | `GET /api/system` | CPU, RAM, đĩa |

```bash
curl -H "Authorization: Bearer $MGMT_KEY" "$VPS/api/status"
```

**Dấu hiệu khoẻ mạnh:** cả 3 service `active: true`. Gateway tự khởi động lại khi lỗi (`Restart=always`).

## 6.2. Xem log

| Thao tác | API |
|----------|-----|
| Đọc log một service | `GET /api/logs?service=hermes-gateway&lines=100` |
| Log realtime (SSE stream) | `GET /api/logs/stream?service=hermes-gateway` |
| Danh sách file log | `GET /api/logs/files` |

Khi bot "không trả lời", xem log gateway trước tiên — thường thấy ngay lỗi key hết hạn, kênh sai token, hoặc plugin chưa bật.

## 6.3. Khởi động lại / dừng / chạy

| Thao tác | API | Khi nào dùng |
|----------|-----|--------------|
| Restart toàn bộ | `POST /api/restart` | Sau khi sửa cấu hình thủ công |
| Dừng toàn bộ | `POST /api/stop` | Bảo trì |
| Chạy lại | `POST /api/start` | Sau bảo trì |
| Cài lại venv Python | `POST /api/rebuild` | Môi trường Python lỗi |

> Các thao tác đổi cấu hình qua dashboard (model, kênh, vai trò, Zalo...) đã **tự restart** — chỉ cần restart thủ công khi sửa file trực tiếp trên VPS.

## 6.4. Nâng cấp

| Thao tác | API | Nội dung |
|----------|-----|----------|
| Nâng cấp Hermes Agent | `POST /api/upgrade` | `git pull` mã nguồn Hermes + cài lại + restart (chạy nền, HTTP 202) |
| Nâng cấp Management API | `POST /api/upgrade-mgmt` | Tải mã mới của mgmt-api + **cập nhật plugin Zalo** (git pull + npm install) + restart |

Sau khi gọi upgrade, theo dõi bằng `GET /api/status` và `GET /api/version`.

## 6.5. Lịch chạy tự động (cron)

Đặt lịch cho bot tự làm việc định kỳ (gửi báo cáo, nhắc lịch...):

| Thao tác | API |
|----------|-----|
| Liệt kê job | `GET /api/cron` |
| Tạo job | `POST /api/cron` |
| Chạy ngay | `POST /api/cron/{job_id}/run` |
| Tạm dừng / chạy lại | `POST /api/cron/{job_id}/pause` · `/resume` |
| Xoá job | `DELETE /api/cron/{job_id}` |
| Trạng thái scheduler | `GET /api/cron/status` |

## 6.6. Biến môi trường (.env)

Xem/sửa cấu hình thô (cho kỹ thuật):

| Thao tác | API |
|----------|-----|
| Liệt kê (giá trị nhạy cảm bị che) | `GET /api/env` |
| Đặt một biến | `PUT /api/env/{KEY}` body `{"value": "..."}` |
| Xoá một biến | `DELETE /api/env/{KEY}` |

## 6.7. Đổi domain

Khi khách có domain riêng (yêu cầu DNS A record đã trỏ về IP VPS **trước**):

```bash
curl -X PUT "$VPS/api/domain" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"domain": "bot.tencongty.vn"}'
```

Caddy tự xin chứng chỉ SSL Let's Encrypt cho domain mới.

## 6.8. Chạy lệnh CLI từ xa

`POST /api/cli` chạy một lệnh `hermes` trong danh sách cho phép: `version, status, doctor, config, model, cron, gateway, logs, skills, sessions, memory, tools, insights, auth`.

```bash
curl -X POST "$VPS/api/cli" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"command": "doctor", "args": []}'
```

`hermes doctor` là lệnh chẩn đoán tổng quát — chạy đầu tiên khi gặp sự cố lạ.

## 6.9. OpenViking (bộ nhớ dài hạn — tuỳ chọn)

OpenViking là backend bộ nhớ/ngữ cảnh nâng cao, **không cài mặc định** (tốn RAM). Bật khi khách cần bot nhớ lâu và tra cứu dữ liệu lớn.

Vòng đời quản lý từ dashboard:

| Thao tác | API |
|----------|-----|
| Trạng thái (đã cài? đang chạy? đã nối Hermes?) | `GET /api/openviking/status` |
| Cài đặt (chạy nền) | `POST /api/openviking/install` |
| Xem / đặt cấu hình (key embedding + VLM) | `GET` / `POST /api/openviking/config` |
| Kiểm tra key trước khi lưu | `POST /api/openviking/test-key` |
| Bật (nối vào Hermes) / Tắt | `POST /api/openviking/enable` · `/disable` |
| Restart / Nâng cấp | `POST /api/openviking/restart` · `/upgrade` |
| Thống kê / Log | `GET /api/openviking/stats` · `/logs` |
| Gỡ cài đặt | `POST /api/openviking/uninstall` |

Trình tự chuẩn: `install` → `config` (đặt 2 key) → `test-key` → `enable`.

## 6.10. Reset hệ thống

`POST /api/reset` — đưa cấu hình Hermes về mặc định. **Thao tác phá huỷ**, chỉ dùng khi bàn giao lại máy hoặc làm lại từ đầu. Sao lưu `/opt/hermes/.env` và `/root/.hermes/` trước khi chạy.
