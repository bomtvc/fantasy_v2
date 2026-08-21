# Flask FPL League Analyzer

Modern web application for analyzing Fantasy Premier League leagues with beautiful glassmorphic UI.

## Features

- 📊 **Dashboard** - Tổng quan league
- 👥 **League Members** - Danh sách thành viên kèm thống kê giải thưởng
- 📈 **GW Points** - Điểm theo từng gameweek
- 📅 **Month Points** - Tổng hợp theo tháng
- 🏆 **Rankings** - Xếp hạng tuần / tháng
- 🏅 **Awards** - Giải thưởng và tiền thưởng
- ⭐ **Top Picks** - Cầu thủ được chọn nhiều nhất
- 🎯 **Chip History** - Lịch sử dùng chip
- 🎉 **Fun Stats** - Captain, ghế dự bị, transfer hay/dở nhất
- 🔄 **Transfer History** - Toàn bộ chuyển nhượng

## Tech Stack

- **Backend**: Flask 3.0+ (application factory + blueprints)
- **Frontend**: Vanilla JS + Modern CSS (Glassmorphism)
- **Data**: Pandas, FPL Public API
- **Cache**: Flask-Caching (FileSystemCache, chia sẻ giữa các gunicorn worker)

## Installation

```bash
pip install -r flask_requirements.txt
cp .env.example .env      # rồi điền SECRET_KEY
```

## Running the App

```bash
python flask_app.py                                  # dev
gunicorn --workers 2 --bind 0.0.0.0:5000 "flask_app:create_app()"   # production
```

Mặc định: <http://127.0.0.1:5000>

## Configuration

Toàn bộ cấu hình nằm trong `config.py` và **có thể override bằng biến môi
trường hoặc file `.env`** (nạp tự động qua `python-dotenv`). Xem `.env.example`
để biết danh sách đầy đủ.

Các biến quan trọng:

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `SECRET_KEY` | *(sinh ngẫu nhiên)* | **Bắt buộc đặt trước khi deploy** |
| `FLASK_DEBUG` | `False` | Không bật ở production |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | |
| `CORS_ORIGINS` | *(trống)* | Trống = tắt CORS. Giao diện cùng origin nên không cần |
| `ADMIN_TOKEN` | *(trống)* | Header `X-Admin-Token` cho `/api/cache/*` khi gọi từ script |
| `MAX_WORKERS` | `6` | Số thread gọi FPL API song song |
| `CACHE_DIR` | `<project>/cache` | Đường dẫn tuyệt đối, không phụ thuộc CWD |

### Chiến lược cache

| Loại dữ liệu | TTL | Lý do |
|---|---|---|
| `bootstrap-static` | 1 giờ | Chứa `events[].finished` - phải cập nhật sớm |
| League standings | 1 giờ | Thay đổi chậm |
| Entry history / transfers | 15 phút | Cập nhật trong lúc GW diễn ra |
| Picks & điểm live của **GW đã chốt** | 7 ngày | Dữ liệu bất biến |

## API Endpoints

| Endpoint | Mô tả |
|---|---|
| `GET /api/health` | Liveness probe |
| `GET /api/current-gw` | `current_gw` + `last_finished_gw` |
| `GET /api/league/<id>` | Thông tin league, leader, best GW |
| `GET /api/league/<id>/entries` | Thành viên + thống kê giải |
| `GET /api/gw-points` | Bảng điểm theo GW |
| `GET /api/month-points` | Bảng điểm theo tháng |
| `GET /api/chip-history` | Lịch sử chip |
| `GET /api/weekly-ranking` | Xếp hạng một GW |
| `GET /api/monthly-ranking` | Xếp hạng một tháng |
| `GET /api/awards-summary` | Người thắng tuần/tháng |
| `GET /api/awards-leaderboard` | Bảng tổng giải thưởng |
| `GET /api/top-picks` | Cầu thủ được chọn nhiều nhất |
| `GET /api/fun-stats` | Captain / bench / transfer hay dở |
| `GET /api/transfer-history` | Lịch sử chuyển nhượng |
| `POST /api/export/csv` | Xuất CSV |
| `POST /api/cache/clear` | Xoá cache *(same-origin hoặc `X-Admin-Token`)* |
| `GET /api/cache/stats` | Thống kê cache *(same-origin hoặc `X-Admin-Token`)* |

Tham số dùng chung: `league_id` (bắt buộc), `phase`, `gw_start`, `gw_end`,
`max_entries`, `month_mapping`.

## Ghi chú về FPL API

- **`event/{gw}/live/`** trả điểm của toàn bộ cầu thủ trong 1 request - app dùng
  endpoint này thay vì gọi `element-summary/{id}/` cho từng cầu thủ.
- **`entry/{id}/history/`** đã chứa khoá `chips` đầy đủ, nên không cần gọi
  `picks` từng GW để dò chip.
- **`leagues-classic/{id}/standings/`**: trước khi GW đầu tiên kết thúc, toàn bộ
  thành viên nằm ở `new_entries` chứ không phải `standings`. App gộp cả hai.
- Một GW được coi là đã chốt khi `events[].finished` **và** `data_checked` cùng
  bằng `true` (điểm bonus đã tính xong).

## Tests

```bash
pytest
```

## Project Structure

```
fantasy/
├── flask_app.py           # Application factory
├── extensions.py          # Flask extensions
├── config.py              # Configuration (env-aware)
├── routes/
│   ├── __init__.py        # Blueprint registration
│   ├── main_routes.py     # Page routes
│   └── api_routes.py      # API endpoints
├── services/
│   ├── fpl_api.py         # FPL API client (session, retry, cache)
│   └── data_processor.py  # Business logic (thuần, dễ test)
├── templates/
│   ├── components/        # navbar, sidebar, loading
│   ├── errors/            # 404, 500
│   └── pages/             # Từng trang
├── static/                # css, js, images
└── tests/                 # pytest
```
