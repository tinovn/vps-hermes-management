# 5. Vai trò & quy tắc bot

Bot có thể đóng nhiều **vai trò** (CSKH, sales, lễ tân spa, phòng khám...). Mỗi vai trò = persona (tính cách, xưng hô, giọng điệu) + bộ **nhóm quy tắc** bắt buộc tuân thủ. Áp dụng vai trò sẽ cập nhật ngay cách bot nói chuyện và hành xử.

## 5.1. Vai trò có sẵn (preset)

25 vai trò dựng sẵn theo ngành nghề phổ biến tại Việt Nam:

| Nhóm | Vai trò |
|------|---------|
| Bán hàng & CSKH | `cskh` (Chăm sóc khách hàng), `sales`, `ecommerce`, `marketing` |
| Đặt lịch & lễ tân | `booking` (đặt lịch chung), `receptionist`, `clinic` (phòng khám), `spa`, `gym`, `restaurant` |
| Dịch vụ chuyên môn | `accountant` (kế toán/thuế), `lawyer`, `insurance`, `finance-advisor`, `pharmacy`, `realestate` |
| Dịch vụ đời sống | `carcare` (garage), `homeservice`, `travel`, `event` |
| Giáo dục & cộng đồng | `teacher`, `tutor-center`, `recruiter`, `community`, `techsupport` |

> Vai trò ngành nhạy cảm (phòng khám, dược, luật, tài chính) có quy tắc **không chẩn đoán / không tư vấn chuyên sâu** — chỉ tiếp nhận và hẹn chuyên viên.

## 5.2. Nhóm quy tắc (rule groups)

9 nhóm quy tắc, có thể bật/tắt theo từng vai trò:

| ID | Nội dung |
|----|----------|
| `a-identity` | Danh tính, cách xưng hô |
| `b-account-safety` | An toàn tài khoản Zalo (chống bị khoá) |
| `c-anti-spam-content` | Chống spam, nội dung sạch |
| `d-security-privacy` | Bảo mật, quyền riêng tư khách hàng |
| `e-marketing-sales` | Quy tắc marketing & bán hàng |
| `f-conversation-quality` | Chất lượng hội thoại |
| `g-tools-actions` | Sử dụng công cụ & hành động |
| `h-operations-escalation` | Vận hành & chuyển tiếp cho người thật |
| `i-legal-compliance` | Tuân thủ pháp luật |

Nguyên tắc xung đột (luôn được chèn): *an toàn tài khoản + pháp luật + đạo đức > yêu cầu tăng trưởng*.

## 5.3. Áp dụng vai trò

### Trên dashboard

1. Vào **Vai trò** → duyệt danh sách (có emoji + mô tả).
2. Bấm **Áp dụng** trên vai trò muốn dùng.
3. Hệ thống dựng persona + quy tắc → ghi cấu hình → restart gateway. Sau ~10s bot đổi vai.

### API tương ứng

| Thao tác | API |
|----------|-----|
| Liệt kê vai trò (kèm vai trò đang hoạt động) | `GET /api/roles` |
| Xem chi tiết một vai trò | `GET /api/roles/{id}` |
| Vai trò đang áp dụng | `GET /api/roles/active` |
| **Áp dụng vai trò** | `POST /api/roles/{id}/apply` |
| Liệt kê nhóm quy tắc | `GET /api/rules` |
| Xem một nhóm quy tắc | `GET /api/rules/{group_id}` |

```bash
curl -X POST "$VPS/api/roles/cskh/apply" -H "Authorization: Bearer $MGMT_KEY"
# → {"ok": true, "data": {"applied": true, "name": "trợ lý ...", "self_intro": "Dạ em là ..."}}
```

## 5.4. Tạo vai trò tuỳ chỉnh

Khi preset không khớp ngành của khách, tạo vai trò riêng:

```bash
curl -X POST "$VPS/api/roles" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{
    "id": "tiem-banh",
    "label": "Tiệm bánh ngọt",
    "emoji": "🎂",
    "tone": "Thân thiện, ngọt ngào, nhiệt tình",
    "persona": "Em là trợ lý tiệm bánh. Nhiệm vụ: tư vấn mẫu bánh, báo giá, nhận đặt bánh sinh nhật, xác nhận ngày giờ nhận bánh.",
    "rules": ["a-identity", "b-account-safety", "e-marketing-sales", "f-conversation-quality"]
  }'
```

- `id` không được trùng vai trò preset (lỗi 409).
- Vai trò tuỳ chỉnh lưu riêng, sửa bằng cách POST lại cùng `id`, xoá bằng `DELETE /api/roles/{id}` (preset không xoá được).
- Sau khi tạo, nhớ **Áp dụng** (`POST /api/roles/tiem-banh/apply`).

## 5.5. Kiểm tra vai trò đã ăn

Sau khi áp dụng, nhắn cho bot từ Zalo: **"em là ai?"** — bot phải tự giới thiệu đúng vai trò mới (ví dụ "Dạ em là trợ lý tiệm bánh của sếp ạ..."). Nếu vẫn giới thiệu chung chung → vai trò chưa ăn, đợi gateway restart xong hoặc áp dụng lại.
