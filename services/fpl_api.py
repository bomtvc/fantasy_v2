"""
FPL API Service
Wrapper for Fantasy Premier League API calls with caching and error handling
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

# Import cache for disk-based persistence
# Note: This creates a circular import risk, so we'll use lazy import
_cache = None


def get_cache():
    """Lazy import cache to avoid circular dependency"""
    global _cache
    if _cache is None:
        from extensions import cache
        _cache = cache
    return _cache


class FPLError(Exception):
    """Custom exception for FPL API errors"""
    pass


class FPLNotFound(FPLError):
    """Raised when the FPL API returns 404 (entry/GW không tồn tại)."""
    pass


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def get_session() -> requests.Session:
    """Session dùng chung: connection pooling + retry tự động + User-Agent.

    Retry được cấu hình ở tầng adapter nên chỉ áp dụng cho lỗi kết nối và các
    mã lỗi tạm thời (429/5xx). Lỗi 4xx như 404 sẽ fail ngay lập tức thay vì
    tốn 3 lần thử + backoff như cách làm thủ công trước đây.
    """
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                session = requests.Session()
                session.headers.update({
                    'User-Agent': config.USER_AGENT,
                    'Accept': 'application/json',
                })
                retry = Retry(
                    total=config.MAX_RETRIES,
                    connect=config.MAX_RETRIES,
                    read=config.MAX_RETRIES,
                    status=config.MAX_RETRIES,
                    backoff_factor=config.RETRY_BACKOFF,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(['GET']),
                    respect_retry_after_header=True,
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(
                    max_retries=retry,
                    pool_connections=config.HTTP_POOL_SIZE,
                    pool_maxsize=config.HTTP_POOL_SIZE,
                )
                session.mount('https://', adapter)
                session.mount('http://', adapter)
                _session = session
    return _session


def fetch_json(url: str, timeout: Optional[int] = None) -> Dict:
    """
    Fetch JSON data from URL with retry logic and error handling

    Args:
        url: API endpoint URL
        timeout: Request timeout in seconds (mặc định config.REQUEST_TIMEOUT)

    Returns:
        Dict: JSON response data

    Raises:
        FPLNotFound: Nếu API trả về 404
        FPLError: Với mọi lỗi khác sau khi đã retry
    """
    timeout = timeout if timeout is not None else config.REQUEST_TIMEOUT

    try:
        response = get_session().get(url, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise FPLError(f"Unable to fetch data from {url}: {exc}") from exc

    if response.status_code == 404:
        raise FPLNotFound(f"Not found: {url}")

    if not response.ok:
        raise FPLError(f"HTTP {response.status_code} from {url}")

    try:
        return response.json()
    except ValueError as exc:
        raise FPLError(f"Invalid JSON from {url}: {exc}") from exc


def _api_url(path: str) -> str:
    return f"{config.FPL_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


# --------------------------------------------------------------------------
# Bootstrap / gameweek metadata
# --------------------------------------------------------------------------

def get_bootstrap_static_raw() -> Dict:
    """
    Get raw bootstrap-static data from FPL API
    Cached on disk (config.BOOTSTRAP_CACHE_TTL)

    Payload này chứa ``events[]`` - nguồn sự thật duy nhất cho việc một GW đã
    kết thúc hay chưa, nên TTL cố tình để ngắn.

    Returns:
        Dict with complete bootstrap-static data

    Raises:
        FPLError: If unable to fetch data
    """
    cache = get_cache()
    cache_key = 'bootstrap_static_raw'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    data = fetch_json(_api_url('bootstrap-static/'))
    cache.set(cache_key, data, timeout=config.BOOTSTRAP_CACHE_TTL)
    return data


def get_bootstrap_static() -> pd.DataFrame:
    """
    Get bootstrap-static data to map player ID to names

    Dùng disk cache thay vì ``lru_cache``: lru_cache không có TTL nên dữ liệu
    bị đóng băng đến khi restart process, và mỗi gunicorn worker lại giữ một
    bản sao riêng.

    Returns:
        DataFrame with player information

    Raises:
        FPLError: If unable to fetch player data
    """
    cache = get_cache()
    cache_key = 'bootstrap_players_df'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    data = get_bootstrap_static_raw()
    players = [
        {
            'id': player['id'],
            'web_name': player['web_name'],
            'first_name': player['first_name'],
            'second_name': player['second_name'],
            'team': player['team'],
            'element_type': player['element_type'],
            'full_name': f"{player['first_name']} {player['second_name']}",
        }
        for player in data.get('elements', [])
    ]

    df = pd.DataFrame(players)
    cache.set(cache_key, df, timeout=config.BOOTSTRAP_CACHE_TTL)
    return df


def get_events() -> List[Dict]:
    """Danh sách 38 gameweek kèm metadata (finished, is_current, ...)."""
    try:
        return get_bootstrap_static_raw().get('events', [])
    except FPLError as exc:
        logger.warning("Unable to fetch events metadata: %s", exc)
        return []


def get_finished_gws() -> Set[int]:
    """Tập các GW đã đá xong VÀ đã chốt điểm bonus (``data_checked``).

    Đây là thứ thay thế cho heuristic cũ ``Points > 0`` - vốn xoá nhầm những
    manager thực sự được 0 điểm ra khỏi bảng xếp hạng.
    """
    return {
        event['id']
        for event in get_events()
        if event.get('finished') and event.get('data_checked')
    }


def is_gw_finished(gw: int) -> bool:
    """GW đã kết thúc và chốt điểm hay chưa (dữ liệu đã bất biến)."""
    return gw in get_finished_gws()


def get_current_gw() -> int:
    """
    GW mới nhất có dữ liệu để hiển thị.

    Ưu tiên GW đang diễn ra (``is_current``) - khác với hành vi cũ luôn trả về
    GW finished gần nhất, khiến dashboard hiển thị GW trước trong lúc vòng đấu
    đang đá.

    Returns:
        Current gameweek number
    """
    events = get_events()

    for event in events:
        if event.get('is_current'):
            return int(event['id'])

    finished = [int(e['id']) for e in events if e.get('finished')]
    if finished:
        return max(finished)

    for event in events:
        if event.get('is_next'):
            return max(1, int(event['id']) - 1)

    return 1  # Ultimate fallback


def get_last_finished_gw() -> int:
    """GW cuối cùng đã chốt điểm - dùng để quyết định giải tuần/tháng.

    Trả về 0 khi mùa giải chưa có GW nào hoàn tất.
    """
    finished = get_finished_gws()
    return max(finished) if finished else 0


# --------------------------------------------------------------------------
# League standings
# --------------------------------------------------------------------------

def _standings_row(entry: Dict) -> Dict:
    return {
        'Team_ID': entry['entry'],
        'Manager': entry.get('player_name') or '-',
        'Team': entry.get('entry_name') or '-',
        'Rank': entry.get('rank', 0),
        'Total': entry.get('total', 0),
    }


def _new_entry_row(entry: Dict) -> Dict:
    """Thành viên vừa vào league, chưa có thứ hạng.

    ``new_entries`` dùng schema khác ``standings``: tên người chơi tách thành
    ``player_first_name``/``player_last_name`` và chưa có ``rank``/``total``.
    """
    name = f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip()
    return {
        'Team_ID': entry['entry'],
        'Manager': name or '-',
        'Team': entry.get('entry_name') or '-',
        'Rank': 0,
        'Total': 0,
    }


def get_all_league_entries(league_id: int, phase: int) -> pd.DataFrame:
    """
    Get all entries in league by automatically discovering all pages
    Cached for 1 hour on disk as league standings change relatively slowly

    Gộp cả ``standings`` lẫn ``new_entries``: trước khi GW đầu tiên kết thúc,
    FPL để **toàn bộ** thành viên trong ``new_entries`` và ``standings`` rỗng,
    nên phiên bản trước báo "Unable to fetch any entries data" suốt giai đoạn
    đầu mùa.

    Nếu một trang lỗi thì hàm này raise thay vì bỏ qua: trước đây trang lỗi bị
    ``continue`` rồi bảng xếp hạng thiếu người vẫn được cache nguyên 1 tiếng.

    Args:
        league_id: FPL league ID
        phase: League phase number

    Returns:
        DataFrame with all league entries

    Raises:
        FPLError: If unable to fetch any entries data
    """
    cache = get_cache()
    cache_key = f'league_entries_{league_id}_{phase}'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    entries_by_id: Dict[int, Dict] = {}
    page_standings = 1
    page_new_entries = 1
    more_standings = True
    more_new_entries = True
    guard = 0
    max_pages = 100  # Safety limit to prevent infinite loop

    while (more_standings or more_new_entries) and guard < max_pages:
        guard += 1
        url = _api_url(
            f'leagues-classic/{league_id}/standings/'
            f'?page_standings={page_standings}'
            f'&page_new_entries={page_new_entries}'
            f'&phase={phase}'
        )
        try:
            data = fetch_json(url)
        except FPLError as exc:
            raise FPLError(
                f"Unable to fetch standings page {page_standings} "
                f"for league {league_id}: {exc}"
            ) from exc

        standings = data.get('standings') or {}
        new_entries = data.get('new_entries') or {}

        # standings có rank/total nên được ưu tiên ghi đè new_entries
        for entry in new_entries.get('results') or []:
            entries_by_id.setdefault(entry['entry'], _new_entry_row(entry))
        for entry in standings.get('results') or []:
            entries_by_id[entry['entry']] = _standings_row(entry)

        more_standings = bool(standings.get('has_next'))
        more_new_entries = bool(new_entries.get('has_next'))
        if more_standings:
            page_standings += 1
        if more_new_entries:
            page_new_entries += 1

        if more_standings or more_new_entries:
            time.sleep(config.REQUEST_DELAY)  # Delay to avoid rate limit

    if not entries_by_id:
        raise FPLError(f"Unable to fetch any entries data for league {league_id}")

    df = pd.DataFrame(list(entries_by_id.values()))
    cache.set(cache_key, df, timeout=config.LEAGUE_CACHE_TTL)
    return df


# --------------------------------------------------------------------------
# Entry data
# --------------------------------------------------------------------------

def get_entry_history(entry_id: int) -> Optional[Dict]:
    """
    Get complete entry history for all gameweeks - optimized single API call
    Cached on disk as GW data updates frequently

    Payload gồm 3 khoá: ``current`` (điểm từng GW), ``past`` (các mùa trước) và
    ``chips`` (danh sách chip đã dùng - đầy đủ, không cần gọi picks để dò).

    Args:
        entry_id: FPL team entry ID

    Returns:
        Dict with complete history data, or None if error
    """
    cache = get_cache()
    cache_key = f'entry_history_{entry_id}'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    try:
        data = fetch_json(_api_url(f'entry/{entry_id}/history/'))
    except FPLError as exc:
        logger.warning("Unable to fetch history for entry %s: %s", entry_id, exc)
        return None

    cache.set(cache_key, data, timeout=config.GW_DATA_CACHE_TTL)
    return data


def get_entry_transfers(entry_id: int) -> Optional[List]:
    """
    Get transfer history for a specific entry
    Cached on disk

    Args:
        entry_id: FPL team entry ID

    Returns:
        List of transfer data, or None if error
    """
    cache = get_cache()
    cache_key = f'entry_transfers_{entry_id}'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    try:
        data = fetch_json(_api_url(f'entry/{entry_id}/transfers/'))
    except FPLError as exc:
        logger.warning("Unable to fetch transfers for entry %s: %s", entry_id, exc)
        return None

    cache.set(cache_key, data, timeout=config.GW_DATA_CACHE_TTL)
    return data


def get_entry_gw_picks(entry_id: int, gw: int) -> Optional[Dict]:
    """
    Get picks and entry history for a specific entry and GW

    Đội hình của một GW đã kết thúc là bất biến nên được cache rất dài
    (``FINISHED_GW_CACHE_TTL``); GW đang diễn ra dùng TTL ngắn.

    Args:
        entry_id: FPL team entry ID
        gw: Gameweek number

    Returns:
        Dict with picks and entry history data, or None if error
    """
    cache = get_cache()
    cache_key = f'entry_picks_{entry_id}_{gw}'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    try:
        data = fetch_json(_api_url(f'entry/{entry_id}/event/{gw}/picks/'))
    except FPLNotFound:
        # Entry chưa tồn tại ở GW này (tham gia muộn) - không phải lỗi.
        return None
    except FPLError as exc:
        logger.warning("Unable to fetch picks for entry %s GW %s: %s", entry_id, gw, exc)
        return None

    if 'entry_history' not in data:
        # Fallback: lấy từ endpoint history
        history_data = get_entry_history(entry_id)
        for event in (history_data or {}).get('current', []):
            if event.get('event') == gw:
                data['entry_history'] = event
                break

    ttl = config.FINISHED_GW_CACHE_TTL if is_gw_finished(gw) else config.GW_DATA_CACHE_TTL
    cache.set(cache_key, data, timeout=ttl)
    return data


# --------------------------------------------------------------------------
# Player points - bulk via event/{gw}/live/
# --------------------------------------------------------------------------

def get_live_gw_points(gw: int) -> Dict[int, int]:
    """Bảng điểm của **toàn bộ** cầu thủ trong một GW, chỉ tốn 1 HTTP request.

    Thay thế cách làm cũ gọi ``element-summary/{id}/`` cho từng cầu thủ: với
    một league lớn cách cũ tốn hàng trăm request và bị giữ trong ``lru_cache``
    vô thời hạn nên điểm không bao giờ cập nhật giữa GW.

    Args:
        gw: Gameweek number

    Returns:
        Dict ``{element_id: total_points}``. Rỗng nếu GW chưa có dữ liệu.
    """
    if not gw:
        return {}

    cache = get_cache()
    cache_key = f'live_gw_points_{gw}'

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    try:
        data = fetch_json(_api_url(f'event/{gw}/live/'))
    except FPLError as exc:
        logger.warning("Unable to fetch live data for GW %s: %s", gw, exc)
        return {}

    points = {
        element['id']: (element.get('stats') or {}).get('total_points', 0)
        for element in data.get('elements', [])
        if element.get('id') is not None
    }

    # GW đã chốt -> dữ liệu bất biến, cache dài. GW đang đá -> TTL ngắn.
    ttl = config.FINISHED_GW_CACHE_TTL if is_gw_finished(gw) else config.GW_DATA_CACHE_TTL
    cache.set(cache_key, points, timeout=ttl)
    return points


def prefetch_live_gw_points(gw_range: List[int]) -> Dict[int, Dict[int, int]]:
    """Nạp song song bảng điểm cho nhiều GW.

    Trả về ``{gw: {element_id: points}}``. Gọi hàm này một lần ở đầu các tác vụ
    nặng để mọi tra cứu điểm sau đó đều là lookup trong bộ nhớ.
    """
    gws = [gw for gw in dict.fromkeys(gw_range) if gw]
    if not gws:
        return {}

    table: Dict[int, Dict[int, int]] = {}
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        for gw, points in zip(gws, executor.map(get_live_gw_points, gws)):
            table[gw] = points
    return table


def get_player_gw_points(element_id: int, gw: int) -> int:
    """Điểm của một cầu thủ trong một GW.

    Chỉ là lookup trên bảng đã nạp bởi :func:`get_live_gw_points`.
    """
    if not element_id or not gw:
        return 0
    return get_live_gw_points(gw).get(element_id, 0)
