# 7. Checklist onboarding khách hàng mới

Quy trình chuẩn triển khai Hermes Agent cho một khách hàng mới, từ lúc có VPS đến lúc bàn giao. Thời gian dự kiến: **30–45 phút** (đã có VPS + thông tin khách).

## Giai đoạn 0 — Thu thập thông tin khách (trước khi cài)

- [ ] Ngành nghề / vai trò bot mong muốn (CSKH, sales, spa, phòng khám...) → chọn preset ở [mục 5](05-vai-tro-quy-tac-bot.md)
- [ ] **Số điện thoại Zalo của sếp** (tài khoản nhận quyền ra lệnh bot)
- [ ] **Tài khoản Zalo riêng cho bot** (SIM riêng, KHÔNG dùng Zalo cá nhân của sếp)
- [ ] Nguồn Model AI: khách có tài khoản **ChatGPT Plus/Pro** không? → dùng Codex OAuth; nếu không → chuẩn bị API key (DeepSeek/Anthropic/...)
- [ ] Kênh chat bổ sung ngoài Zalo? (Telegram, Discord...) → chuẩn bị token tương ứng
- [ ] Domain riêng? → yêu cầu khách trỏ DNS A record về IP VPS trước

## Giai đoạn 1 — Cài đặt VPS

- [ ] VPS Ubuntu 24.04, tối thiểu 1 vCPU / 2 GB RAM (4 GB nếu dùng voice)
- [ ] Chạy script cài đặt:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | bash
  ```
- [ ] **Lưu lại `MGMT_API_KEY` + URL dashboard** từ output cài đặt (gửi vào kho mật khẩu nội bộ, KHÔNG gửi chat thường)
- [ ] Kiểm tra: `curl -H "Authorization: Bearer $MGMT_KEY" $VPS/api/status` → 3 service `active: true`
- [ ] (Nếu có domain riêng) `PUT /api/domain` → mở `https://domain` xác nhận SSL xanh

## Giai đoạn 2 — Tài khoản đăng nhập

- [ ] Tạo user cho khách: `POST /api/auth/create-user` ([mục 1.2](01-dang-nhap-dashboard.md))
- [ ] Thử đăng nhập tại `https://<domain>/login` bằng tài khoản vừa tạo
- [ ] Hướng dẫn khách đổi mật khẩu ngay lần đầu ([mục 1.4](01-dang-nhap-dashboard.md))

## Giai đoạn 3 — Model AI

**Nhánh A — Khách có ChatGPT Plus/Pro (khuyên dùng):**
- [ ] Dashboard → Model AI → **Kết nối ChatGPT** → đưa link + mã cho khách tự đăng nhập ([mục 2.3](02-cau-hinh-model-ai.md))
- [ ] Chờ trạng thái **Đã kết nối** (hệ thống tự chuyển provider + restart)

**Nhánh B — Dùng API key:**
- [ ] `POST /api/config/test-key` xác nhận key sống
- [ ] `PUT /api/config/provider` chọn provider + model
- [ ] `PUT /api/config/api-key` lưu key

- [ ] **Nghiệm thu:** mở dashboard chat `https://<domain>/` → gửi "xin chào" → bot trả lời

## Giai đoạn 4 — Kết nối Zalo

- [ ] Dashboard → Zalo → **Kết nối Zalo** → khách quét QR bằng **tài khoản bot** ([mục 3.1](03-ket-noi-zalo-ca-nhan.md))
- [ ] Trạng thái = `connected`
- [ ] Nhập **số Zalo của sếp** vào ô Owner → Lưu ([mục 3.2](03-ket-noi-zalo-ca-nhan.md))
- [ ] `GET /api/zalo/status` → `owner_set: true`
- [ ] **Nghiệm thu:** sếp nhắn thử cho bot từ Zalo cá nhân → bot phản hồi

## Giai đoạn 5 — Vai trò & kênh bổ sung

- [ ] Áp dụng vai trò theo ngành: `POST /api/roles/{id}/apply` ([mục 5.3](05-vai-tro-quy-tac-bot.md))
- [ ] **Nghiệm thu vai trò:** nhắn "em là ai?" → bot giới thiệu đúng vai trò
- [ ] (Tuỳ chọn) Tạo vai trò custom nếu preset không khớp ([mục 5.4](05-vai-tro-quy-tac-bot.md))
- [ ] (Tuỳ chọn) Bật Telegram/Discord/Slack ([mục 4](04-cau-hinh-kenh-chat.md)) — với Telegram cân nhắc đặt `allowed_users`
- [ ] (Tuỳ chọn) Bật OpenViking nếu khách cần bộ nhớ dài hạn ([mục 6.9](06-van-hanh-giam-sat.md)) — chỉ khi VPS ≥ 4 GB RAM

## Giai đoạn 6 — Nghiệm thu tổng & bàn giao

- [ ] Chạy chẩn đoán: `POST /api/cli` với `{"command": "doctor"}` → không lỗi đỏ
- [ ] `GET /api/system` → RAM còn trống > 20%
- [ ] Test kịch bản thực tế theo ngành của khách (hỏi giá, đặt lịch, khiếu nại...) — tối thiểu 5 hội thoại
- [ ] Kiểm tra bot từ chối đúng các yêu cầu vi phạm (hỏi thông tin khách khác, nội dung cấm...)
- [ ] Bàn giao cho khách:
  - [ ] URL dashboard + tài khoản đăng nhập (khách đã đổi mật khẩu)
  - [ ] Hướng dẫn nhanh: cách xem trạng thái, cách ngắt/kết nối lại Zalo, cách đổi vai trò
  - [ ] Kênh hỗ trợ kỹ thuật + cam kết SLA
- [ ] **KHÔNG bàn giao** `MGMT_API_KEY` trừ khi khách có đội kỹ thuật riêng (key này có toàn quyền hệ thống)

## Giai đoạn 7 — Theo dõi sau bàn giao (tuần đầu)

- [ ] Ngày 1–2: xem log gateway mỗi ngày (`GET /api/logs?service=hermes-gateway`), soát các câu bot trả lời chưa đạt
- [ ] Tinh chỉnh persona/vai trò theo phản hồi của khách
- [ ] Ngày 7: gọi xác nhận hài lòng + chốt các chỉnh sửa cuối

---

## Bảng tổng hợp lỗi thường gặp khi onboarding

| Vấn đề | Tham chiếu |
|--------|-----------|
| Không đăng nhập được dashboard | [Mục 1.6](01-dang-nhap-dashboard.md) |
| Bot không trả lời sau khi đổi model | [Mục 2.5](02-cau-hinh-model-ai.md) |
| Đổi provider nhưng bot vẫn dùng ChatGPT | [Mục 2.3 — phải disable Codex trước](02-cau-hinh-model-ai.md) |
| QR Zalo không hiện / set-owner lỗi | [Mục 3.5](03-ket-noi-zalo-ca-nhan.md) |
| Kênh chat lưu token xong không chạy | [Mục 4.7](04-cau-hinh-kenh-chat.md) |
| Vai trò áp dụng nhưng bot không đổi | [Mục 5.5](05-vai-tro-quy-tac-bot.md) |
