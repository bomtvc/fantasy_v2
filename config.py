# Cấu hình cho FPL League Analyzer
#
# Mọi hằng số dưới đây đều có thể override bằng biến môi trường (hoặc file .env).
# Xem .env.example để biết danh sách biến được hỗ trợ.

import os

from dotenv import load_dotenv

# Thư mục gốc của project - dùng để build đường dẫn tuyệt đối,
# tránh phụ thuộc vào current working directory (systemd/gunicorn hay đổi CWD).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Nạp .env nếu có (không ghi đè biến môi trường đã set sẵn từ hệ thống)
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env_str(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Flask Settings
SECRET_KEY = _env_str("SECRET_KEY", "")
DEBUG = _env_bool("FLASK_DEBUG", False)
HOST = _env_str("HOST", "127.0.0.1")
PORT = _env_int("PORT", 5000)
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

# Domain được phép gọi /api/* qua CORS. "*" chỉ nên dùng khi dev.
CORS_ORIGINS = [o.strip() for o in _env_str("CORS_ORIGINS", "*").split(",") if o.strip()]

# Token bảo vệ các endpoint quản trị (/api/cache/clear, /api/cache/stats).
# Để trống nghĩa là không bật bảo vệ - chỉ chấp nhận được khi chạy local.
ADMIN_TOKEN = _env_str("ADMIN_TOKEN", "")

# API Settings
FPL_BASE_URL = _env_str("FPL_BASE_URL", "https://fantasy.premierleague.com/api")
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 10)
MAX_RETRIES = _env_int("MAX_RETRIES", 3)
RETRY_BACKOFF = _env_float("RETRY_BACKOFF", 0.5)  # urllib3 backoff_factor
REQUEST_DELAY = _env_float("REQUEST_DELAY", 0.3)  # Delay giữa các trang standings (seconds)
USER_AGENT = _env_str(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; FPL-League-Analyzer/2.0; +https://github.com/bomtvc/fantasy)",
)

# Threading Settings
MAX_WORKERS = _env_int("MAX_WORKERS", 6)  # Số thread tối đa cho concurrent requests
HTTP_POOL_SIZE = _env_int("HTTP_POOL_SIZE", max(MAX_WORKERS * 2, 10))

# Cache Settings
CACHE_DIR = _env_str("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
CACHE_TYPE = _env_str("CACHE_TYPE", "FileSystemCache")  # Disk-based cache để tồn tại qua restart
# Ngưỡng phải đủ lớn: picks được cache theo (entry, gw) nên với league 50 người
# x 38 GW đã là ~1900 entry cache, chưa kể các bảng đã tính sẵn.
CACHE_THRESHOLD = _env_int("CACHE_THRESHOLD", 50000)
CACHE_DEFAULT_TIMEOUT = _env_int("CACHE_DEFAULT_TIMEOUT", 300)  # Default timeout: 5 minutes

# TTL Settings - Different values for different data types
# bootstrap-static chứa events[].finished - thứ quyết định GW nào đã chốt,
# nên TTL phải ngắn (1 request/giờ là không đáng kể) thay vì 24h như trước.
BOOTSTRAP_CACHE_TTL = _env_int("BOOTSTRAP_CACHE_TTL", 3600)  # 1 hour
LEAGUE_CACHE_TTL = _env_int("LEAGUE_CACHE_TTL", 3600)  # 1 hour (semi-static league standings)
GW_DATA_CACHE_TTL = _env_int("GW_DATA_CACHE_TTL", 900)  # 15 minutes (dynamic GW points)
API_CACHE_TTL = _env_int("API_CACHE_TTL", 300)  # 5 minutes (general API responses)
# Dữ liệu của GW đã kết thúc là bất biến -> cache rất dài (picks, điểm live theo GW)
FINISHED_GW_CACHE_TTL = _env_int("FINISHED_GW_CACHE_TTL", 604800)  # 7 days

# Default Values
DEFAULT_LEAGUE_ID = _env_int("DEFAULT_LEAGUE_ID", 1644718)
DEFAULT_PHASE = _env_int("DEFAULT_PHASE", 1)
DEFAULT_MONTH_MAPPING = _env_str(
    "DEFAULT_MONTH_MAPPING",
    "1-4,5-8,9-12,13-16,17-20,21-24,25-28,29-32,33-36,37-38",
)

# Prize Money Settings (VND)
WEEKLY_PRIZE = _env_int("WEEKLY_PRIZE", 300000)
MONTHLY_PRIZE = _env_int("MONTHLY_PRIZE", 500000)

# UI Settings
PAGE_TITLE = _env_str("PAGE_TITLE", "RSC Fantasy League")
PAGE_ICON = _env_str("PAGE_ICON", "⚽")

# Export Settings
CSV_ENCODING = "utf-8"
CSV_INDEX = False
