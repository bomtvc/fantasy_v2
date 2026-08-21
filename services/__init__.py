"""
Services Package
Business logic and data processing services
"""

from .fpl_api import (
    FPLError,
    FPLNotFound,
    fetch_json,
    get_bootstrap_static,
    get_bootstrap_static_raw,
    get_events,
    get_finished_gws,
    is_gw_finished,
    get_current_gw,
    get_last_finished_gw,
    get_all_league_entries,
    get_entry_history,
    get_entry_gw_picks,
    get_entry_transfers,
    get_live_gw_points,
    prefetch_live_gw_points,
    get_player_gw_points,
)

from .data_processor import (
    parse_month_mapping,
    build_gw_points_table,
    build_month_points_table,
    build_month_points_table_full,
    build_transfer_history_table,
    get_entry_chips_optimized,
    build_chip_history_table,
    tally_awards,
    calculate_awards_statistics,
    create_ranking_table,
    build_weekly_ranking,
    build_monthly_ranking,
    build_awards_summary_table,
    build_awards_leaderboard,
    build_fun_stats_table,
    build_top_picks_table,
)

__all__ = [
    # Exceptions
    'FPLError',
    'FPLNotFound',

    # FPL API functions
    'fetch_json',
    'get_bootstrap_static',
    'get_bootstrap_static_raw',
    'get_events',
    'get_finished_gws',
    'is_gw_finished',
    'get_current_gw',
    'get_last_finished_gw',
    'get_all_league_entries',
    'get_entry_history',
    'get_entry_gw_picks',
    'get_entry_transfers',
    'get_live_gw_points',
    'prefetch_live_gw_points',
    'get_player_gw_points',

    # Data processor functions
    'parse_month_mapping',
    'build_gw_points_table',
    'build_month_points_table',
    'build_month_points_table_full',
    'build_transfer_history_table',
    'get_entry_chips_optimized',
    'build_chip_history_table',
    'tally_awards',
    'calculate_awards_statistics',
    'create_ranking_table',
    'build_weekly_ranking',
    'build_monthly_ranking',
    'build_awards_summary_table',
    'build_awards_leaderboard',
    'build_fun_stats_table',
    'build_top_picks_table',
]
