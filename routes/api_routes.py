"""
API Routes
RESTful API endpoints for AJAX calls
"""

import glob
import hmac
import io
import logging
import os
import re
from functools import wraps
from urllib.parse import urlsplit

import pandas as pd
from flask import jsonify, request, send_file

import config
from extensions import cache
from services import (
    FPLError,
    get_bootstrap_static,
    get_current_gw,
    get_last_finished_gw,
    get_finished_gws,
    get_all_league_entries,
    build_gw_points_table,
    build_month_points_table_full,
    build_transfer_history_table,
    build_chip_history_table,
    parse_month_mapping,
    calculate_awards_statistics,
    build_weekly_ranking,
    build_monthly_ranking,
    build_awards_summary_table,
    build_awards_leaderboard,
    build_fun_stats_table,
    build_top_picks_table,
)
from . import api_bp

logger = logging.getLogger(__name__)

_SAFE_FILENAME = re.compile(r'[^A-Za-z0-9._-]')


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _fail(message: str, status: int = 400):
    return jsonify({'success': False, 'error': message}), status


def api_error_handler(view):
    """Chuyển exception thành JSON thống nhất và ghi log kèm traceback.

    Lỗi từ FPL API trả 502 (upstream) thay vì 500 để phân biệt với bug của app.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except FPLError as exc:
            logger.warning("Upstream FPL error in %s: %s", view.__name__, exc)
            return _fail(str(exc), 502)
        except Exception as exc:  # noqa: BLE001 - biên ngoài cùng của request
            logger.exception("Unhandled error in %s", view.__name__)
            return _fail(f"{type(exc).__name__}: {exc}", 500)
    return wrapper


def _is_same_origin() -> bool:
    """Request có xuất phát từ chính trang này không.

    Chặn CSRF: trình duyệt vẫn *gửi* POST cross-site dù CORS chặn phần đọc
    response, nên nếu không kiểm tra Origin thì bất kỳ trang web nào cũng xoá
    được cache của app và ép nó crawl lại toàn bộ FPL API.
    """
    source = request.headers.get('Origin') or request.headers.get('Referer')
    if not source:
        return False
    return urlsplit(source).netloc == urlsplit(request.host_url).netloc


def require_admin(view):
    """Bảo vệ endpoint quản trị: token hợp lệ, hoặc request cùng origin.

    - Script/CLI: gửi header ``X-Admin-Token``.
    - Nút bấm trong chính giao diện app: được chấp nhận vì cùng origin.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        supplied = request.headers.get('X-Admin-Token', '')
        if config.ADMIN_TOKEN and hmac.compare_digest(supplied, config.ADMIN_TOKEN):
            return view(*args, **kwargs)

        if _is_same_origin():
            return view(*args, **kwargs)

        logger.warning("Rejected admin request to %s from %s", request.path, request.remote_addr)
        return _fail('Forbidden: same-origin request or valid X-Admin-Token required', 403)
    return wrapper


def get_gw_points_table(league_id: int, phase: int, gw_start: int, gw_end: int,
                        max_entries=None) -> pd.DataFrame:
    """Bảng điểm theo GW, cache dùng chung giữa các endpoint.

    Dashboard, League Members, Rankings và Awards đều cần bảng này. Trước đây
    mỗi endpoint tự dựng lại từ đầu với cache key riêng, nên chỉ mở trang chủ
    đã kéo toàn bộ league hai lần.
    """
    cache_key = f'gw_points_table_{league_id}_{phase}_{gw_start}_{gw_end}_{max_entries}'

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    entries_df = get_all_league_entries(league_id, phase)
    gw_range = list(range(gw_start, gw_end + 1))
    df = build_gw_points_table(entries_df, gw_range, max_entries)

    cache.set(cache_key, df, timeout=config.GW_DATA_CACHE_TTL)
    return df


def _int_arg(name: str, default=None):
    return request.args.get(name, default, type=int)


def _common_args():
    """Bộ tham số lặp lại ở hầu hết endpoint."""
    return {
        'league_id': _int_arg('league_id'),
        'phase': _int_arg('phase', 1),
        'gw_start': _int_arg('gw_start', 1),
        'gw_end': _int_arg('gw_end', 38),
        'max_entries': _int_arg('max_entries'),
        'month_mapping_str': request.args.get('month_mapping', config.DEFAULT_MONTH_MAPPING),
    }


def _table_response(df: pd.DataFrame, **extra):
    payload = {
        'success': True,
        'data': df.to_dict('records'),
        'columns': df.columns.tolist(),
    }
    payload.update(extra)
    return jsonify(payload)


# --------------------------------------------------------------------------
# Gameweek metadata
# --------------------------------------------------------------------------

@api_bp.route('/current-gw')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL)
def get_current_gw_endpoint():
    """Get current gameweek from FPL API"""
    return jsonify({
        'success': True,
        'current_gw': get_current_gw(),
        # GW đã chốt điểm - dùng để biết dữ liệu nào là chính thức
        'last_finished_gw': get_last_finished_gw(),
    })


# --------------------------------------------------------------------------
# League
# --------------------------------------------------------------------------

@api_bp.route('/league/<int:league_id>')
@api_error_handler
@cache.cached(timeout=config.LEAGUE_CACHE_TTL, query_string=True)
def get_league(league_id):
    """Get league basic information including leader and current GW"""
    phase = _int_arg('phase', 1)
    entries_df = get_all_league_entries(league_id, phase)
    current_gw = get_current_gw()

    league_leader = '-'
    leader_points = 0
    if not entries_df.empty and 'Total' in entries_df.columns:
        max_idx = entries_df['Total'].idxmax()
        league_leader = entries_df.loc[max_idx, 'Manager']
        leader_points = int(entries_df.loc[max_idx, 'Total'])

    best_gw_points = 0
    best_gw_manager = '-'
    try:
        gw_points_df = get_gw_points_table(league_id, phase, 1, current_gw)
        if not gw_points_df.empty and 'Points' in gw_points_df.columns:
            max_idx = gw_points_df['Points'].idxmax()
            best_gw_points = int(gw_points_df.loc[max_idx, 'Points'])
            best_gw_manager = gw_points_df.loc[max_idx, 'Manager']
    except FPLError as exc:
        logger.warning("Could not compute best GW points for league %s: %s", league_id, exc)

    return jsonify({
        'success': True,
        'data': {
            'total_entries': len(entries_df),
            'league_id': league_id,
            'phase': phase,
            'current_gw': current_gw,
            'last_finished_gw': get_last_finished_gw(),
            'league_leader': league_leader,
            'leader_points': leader_points,
            'best_gw_points': best_gw_points,
            'best_gw_manager': best_gw_manager,
        }
    })


@api_bp.route('/league/<int:league_id>/entries')
@api_error_handler
@cache.cached(timeout=config.LEAGUE_CACHE_TTL, query_string=True)
def get_league_entries_api(league_id):
    """Get all league entries with awards statistics"""
    phase = _int_arg('phase', 1)
    max_entries = _int_arg('max_entries')
    month_mapping_str = request.args.get('month_mapping', config.DEFAULT_MONTH_MAPPING)

    entries_df = get_all_league_entries(league_id, phase)
    if max_entries:
        entries_df = entries_df.head(max_entries)

    try:
        gw_points_df = get_gw_points_table(league_id, phase, 1, get_current_gw())
        month_mapping = parse_month_mapping(month_mapping_str)
        awards_df = calculate_awards_statistics(gw_points_df, month_mapping)

        entries_df = entries_df.merge(
            awards_df[['Team_ID', 'Weekly_Wins', 'Monthly_Wins', 'Total_Prize_Money']],
            on='Team_ID',
            how='left'
        )
    except FPLError as exc:
        logger.warning("Could not calculate awards for league %s: %s", league_id, exc)
        for column in ('Weekly_Wins', 'Monthly_Wins', 'Total_Prize_Money'):
            entries_df[column] = 0

    for column in ('Weekly_Wins', 'Monthly_Wins', 'Total_Prize_Money'):
        entries_df[column] = entries_df[column].fillna(0).astype(int)

    return jsonify({'success': True, 'data': entries_df.to_dict('records')})


# --------------------------------------------------------------------------
# Points tables
# --------------------------------------------------------------------------

@api_bp.route('/gw-points')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_gw_points():
    """Get GW points table"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], args['gw_end'], args['max_entries']
    )
    gw_points_df = gw_points_df.copy()
    gw_points_df['GW'] = gw_points_df['GW'].astype(int)

    display_df = gw_points_df.pivot_table(
        index=['Manager', 'Team', 'Team_ID'],
        columns='GW',
        values='Points',
        fill_value=0
    ).reset_index()

    # Tên cột số (từ giá trị GW) -> chuỗi, tránh lỗi so sánh kiểu trong JS
    display_df.columns = [str(col) if isinstance(col, int) else col for col in display_df.columns]

    for col in display_df.columns:
        if pd.api.types.is_numeric_dtype(display_df[col]):
            display_df[col] = display_df[col].astype(int)

    return _table_response(display_df)


@api_bp.route('/month-points')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_month_points():
    """Get month points table"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    month_mapping = parse_month_mapping(args['month_mapping_str'])
    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], args['gw_end'], args['max_entries']
    )
    month_df = build_month_points_table_full(gw_points_df, month_mapping)
    return _table_response(month_df)


@api_bp.route('/chip-history')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_chip_history_api():
    """Get chip history"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    entries_df = get_all_league_entries(args['league_id'], args['phase'])
    gw_range = list(range(args['gw_start'], args['gw_end'] + 1))
    chip_df = build_chip_history_table(entries_df, gw_range, args['max_entries'])

    display_df = chip_df.pivot_table(
        index=['Manager', 'Team'],
        columns='GW',
        values='Active_Chip',
        aggfunc='first',
        fill_value='-'
    ).reset_index()

    display_df = display_df.rename(
        columns={col: f'GW{col}' for col in display_df.columns if isinstance(col, int)}
    )
    display_df.columns = [str(col) for col in display_df.columns]

    return _table_response(display_df)


# --------------------------------------------------------------------------
# Rankings & awards
# --------------------------------------------------------------------------

@api_bp.route('/weekly-ranking')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_weekly_ranking():
    """Get weekly ranking for a specific GW"""
    args = _common_args()
    gw = _int_arg('gw')

    if not args['league_id']:
        return _fail('league_id required')
    if not gw:
        return _fail('gw required')

    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], args['gw_end'], args['max_entries']
    )
    return _table_response(build_weekly_ranking(gw_points_df, gw))


@api_bp.route('/monthly-ranking')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_monthly_ranking():
    """Get monthly ranking for a specific month"""
    args = _common_args()
    month = _int_arg('month')

    if not args['league_id']:
        return _fail('league_id required')
    if not month:
        return _fail('month required')

    month_mapping = parse_month_mapping(args['month_mapping_str'])
    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], args['gw_end'], args['max_entries']
    )
    return _table_response(build_monthly_ranking(gw_points_df, month_mapping, month))


@api_bp.route('/awards-summary')
@api_error_handler
@cache.cached(timeout=config.API_CACHE_TTL, query_string=True)
def get_awards_summary():
    """Get awards summary table (weekly and monthly winners)"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    month_mapping = parse_month_mapping(args['month_mapping_str'])
    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], args['gw_end'], args['max_entries']
    )
    summary_df = build_awards_summary_table(gw_points_df, month_mapping, get_finished_gws())
    return _table_response(summary_df)


@api_bp.route('/awards-leaderboard')
@api_error_handler
@cache.cached(timeout=config.API_CACHE_TTL, query_string=True)
def get_awards_leaderboard():
    """Get awards leaderboard"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    # Không tính các GW chưa chốt điểm
    finished_gws = get_finished_gws()
    gw_end = min(args['gw_end'], max(finished_gws) if finished_gws else args['gw_start'])

    month_mapping = parse_month_mapping(args['month_mapping_str'])
    gw_points_df = get_gw_points_table(
        args['league_id'], args['phase'], args['gw_start'], gw_end, args['max_entries']
    )
    leaderboard_df = build_awards_leaderboard(gw_points_df, month_mapping, finished_gws)
    return _table_response(leaderboard_df)


# --------------------------------------------------------------------------
# Fun stats & transfers
# --------------------------------------------------------------------------

@api_bp.route('/fun-stats')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_fun_stats_api():
    """Get fun statistics"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    entries_df = get_all_league_entries(args['league_id'], args['phase'])
    bootstrap_df = get_bootstrap_static()
    gw_range = list(range(args['gw_start'], args['gw_end'] + 1))
    fun_stats_df = build_fun_stats_table(entries_df, gw_range, bootstrap_df, args['max_entries'])
    return _table_response(fun_stats_df)


@api_bp.route('/top-picks')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_top_picks_api():
    """Cầu thủ được chọn nhiều nhất trong league."""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    top_n = _int_arg('top_n', 20)
    entries_df = get_all_league_entries(args['league_id'], args['phase'])
    bootstrap_df = get_bootstrap_static()
    gw_range = list(range(args['gw_start'], args['gw_end'] + 1))

    top_picks_df = build_top_picks_table(
        entries_df, gw_range, bootstrap_df, args['max_entries'], top_n
    )
    return _table_response(top_picks_df)


@api_bp.route('/transfer-history')
@api_error_handler
@cache.cached(timeout=config.GW_DATA_CACHE_TTL, query_string=True)
def get_transfer_history_api():
    """Get transfer history for all managers by GW"""
    args = _common_args()
    if not args['league_id']:
        return _fail('league_id required')

    entries_df = get_all_league_entries(args['league_id'], args['phase'])
    bootstrap_df = get_bootstrap_static()
    transfer_df = build_transfer_history_table(entries_df, bootstrap_df, args['max_entries'])

    if transfer_df.empty:
        return jsonify({
            'success': True,
            'data': [],
            'columns': ['Manager', 'Team'],
            'raw_data': [],
            'stats': {},
        })

    grouped_transfers = transfer_df.groupby(['Manager', 'Team', 'GW'])['Transfer'].apply(
        lambda x: ' | '.join(x)
    ).reset_index()

    transfer_pivot = grouped_transfers.pivot_table(
        index=['Manager', 'Team'],
        columns='GW',
        values='Transfer',
        fill_value='-',
        aggfunc='first'
    )
    transfer_pivot.columns = [f'GW{col}' for col in transfer_pivot.columns]
    transfer_pivot = transfer_pivot.reset_index().sort_values('Manager')

    available_gws = sorted(
        int(col[2:]) for col in transfer_pivot.columns if col.startswith('GW')
    )
    transfer_pivot = transfer_pivot[['Manager', 'Team'] + [f'GW{gw}' for gw in available_gws]]

    stats = {
        'total_transfers': len(transfer_df),
        'transfers_by_gw': transfer_df.groupby('GW').size().to_dict(),
        'transfers_by_manager': transfer_df.groupby('Manager').size().to_dict(),
        'most_transferred_in': transfer_df['Player_In'].value_counts().head(5).to_dict(),
        'most_transferred_out': transfer_df['Player_Out'].value_counts().head(5).to_dict(),
    }

    return _table_response(
        transfer_pivot,
        raw_data=transfer_df.to_dict('records'),
        stats=stats,
    )


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

@api_bp.route('/export/csv', methods=['POST'])
@api_error_handler
def export_csv():
    """Export data to CSV"""
    payload = request.get_json(silent=True) or {}
    data = payload.get('data', [])

    if not data:
        return _fail('No data provided')

    # Tên file do client gửi -> chỉ giữ ký tự an toàn, chặn path traversal và
    # ký tự điều khiển lọt vào header Content-Disposition.
    raw_name = str(payload.get('filename', 'export.csv'))
    filename = _SAFE_FILENAME.sub('_', os.path.basename(raw_name)) or 'export.csv'
    if not filename.lower().endswith('.csv'):
        filename += '.csv'

    buffer = io.BytesIO()
    buffer.write(pd.DataFrame(data).to_csv(index=False).encode(config.CSV_ENCODING))
    buffer.seek(0)

    return send_file(buffer, mimetype='text/csv', as_attachment=True, download_name=filename)


# --------------------------------------------------------------------------
# Cache administration
# --------------------------------------------------------------------------

@api_bp.route('/cache/clear', methods=['POST'])
@require_admin
@api_error_handler
def clear_cache():
    """Clear application cache"""
    cache.clear()

    removed = 0
    if os.path.isdir(config.CACHE_DIR):
        for path in glob.glob(os.path.join(config.CACHE_DIR, '*')):
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove cache file %s: %s", path, exc)

    logger.info("Cache cleared (%s files removed)", removed)
    return jsonify({
        'success': True,
        'message': f'Cache cleared ({removed} files removed)',
    })


@api_bp.route('/cache/stats', methods=['GET'])
@require_admin
@api_error_handler
def get_cache_stats():
    """Get cache statistics"""
    cache_files = glob.glob(os.path.join(config.CACHE_DIR, '*'))
    total_size = sum(os.path.getsize(f) for f in cache_files if os.path.isfile(f))

    if total_size < 1024:
        size_str = f"{total_size} B"
    elif total_size < 1024 * 1024:
        size_str = f"{total_size / 1024:.2f} KB"
    else:
        size_str = f"{total_size / (1024 * 1024):.2f} MB"

    return jsonify({
        'success': True,
        'stats': {
            'cache_type': config.CACHE_TYPE,
            'cache_dir': config.CACHE_DIR,
            'total_files': len(cache_files),
            'total_size': total_size,
            'total_size_human': size_str,
            'cache_threshold': config.CACHE_THRESHOLD,
            'ttl_config': {
                'bootstrap': f"{config.BOOTSTRAP_CACHE_TTL}s",
                'league': f"{config.LEAGUE_CACHE_TTL}s",
                'gw_data': f"{config.GW_DATA_CACHE_TTL}s",
                'finished_gw': f"{config.FINISHED_GW_CACHE_TTL}s",
                'api_default': f"{config.API_CACHE_TTL}s",
            },
        },
    })


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------

@api_bp.route('/health')
def health():
    """Liveness probe cho reverse proxy / systemd."""
    return jsonify({'success': True, 'status': 'ok'})
