"""
Data Processor Service
Business logic for FPL data calculations and aggregations
"""

import logging
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import config
from .fpl_api import (
    FPLError,
    get_entry_history,
    get_entry_transfers,
    get_entry_gw_picks,
    get_current_gw,
    get_finished_gws,
    prefetch_live_gw_points,
)

logger = logging.getLogger(__name__)


def parse_month_mapping(mapping_str: str) -> Dict[int, int]:
    """
    Parse month mapping string to dict {gw: month}
    Example: "1-4,5-9,10-13" -> {1:1, 2:1, 3:1, 4:1, 5:2, 6:2, ...}
    
    Args:
        mapping_str: Comma-separated ranges (e.g., "1-4,5-8,9-12")
        
    Returns:
        Dict mapping GW number to month number
    """
    gw_to_month = {}
    
    try:
        ranges = mapping_str.split(',')
        for month_idx, range_str in enumerate(ranges, 1):
            if '-' in range_str:
                start, end = map(int, range_str.strip().split('-'))
                for gw in range(start, end + 1):
                    gw_to_month[gw] = month_idx
            else:
                gw = int(range_str.strip())
                gw_to_month[gw] = month_idx
    except (ValueError, AttributeError) as exc:
        logger.error("Invalid month mapping %r: %s", mapping_str, exc)
        return {}
    
    return gw_to_month


def build_gw_points_table(entries_df: pd.DataFrame, gw_range: List[int], 
                          max_entries: Optional[int] = None,
                          progress_callback=None) -> pd.DataFrame:
    """
    Build gameweek points table for all entries using optimized history API
    
    Args:
        entries_df: DataFrame with league entries
        gw_range: List of gameweek numbers to process
        max_entries: Optional limit on number of entries
        progress_callback: Optional callback function(current, total, message)
        
    Returns:
        DataFrame with GW points data for all entries
        
    Raises:
        FPLError: If unable to fetch points data for any entry
    """
    if max_entries:
        entries_df = entries_df.head(max_entries)

    gw_set = set(gw_range)
    results = []
    total_requests = len(entries_df)

    def _blank_row(team_id, manager, team, gw):
        """Hàng giữ chỗ cho GW mà entry không có dữ liệu.

        ``Has_Data=False`` phân biệt "chưa/không có dữ liệu" với "thực sự được
        0 điểm" - trước đây cả hai đều bị lọc bằng ``Points > 0`` nên manager
        được 0 điểm thật bị xoá khỏi bảng xếp hạng.
        """
        return {
            'Team_ID': team_id, 'Manager': manager, 'Team': team, 'GW': int(gw),
            'Points': 0, 'Total_Points': 0, 'Transfers': 0, 'Transfer_Cost': 0,
            'Bench_Points': 0, 'Has_Data': False,
        }

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        # Submit all tasks - one per entry instead of per entry per GW
        future_to_info = {}
        for _, entry in entries_df.iterrows():
            future = executor.submit(get_entry_history, entry['Team_ID'])
            future_to_info[future] = (entry['Team_ID'], entry['Manager'], entry['Team'])

        # Collect results
        completed = 0
        for future in as_completed(future_to_info):
            team_id, manager, team = future_to_info[future]
            completed += 1

            # Update progress
            if progress_callback:
                progress = completed / total_requests
                progress_callback(completed, total_requests,
                                  f"Fetching data: {completed}/{total_requests} ({progress:.1%})")

            existing_gws = set()
            try:
                data = future.result()
            except Exception as exc:
                logger.warning("Error processing entry %s: %s", team_id, exc)
                data = None

            for event in (data or {}).get('current', []):
                gw = event.get('event')
                if gw is None:
                    continue
                gw = int(gw)
                existing_gws.add(gw)
                if gw in gw_set:
                    results.append({
                        'Team_ID': team_id,
                        'Manager': manager,
                        'Team': team,
                        'GW': gw,
                        'Points': event.get('points', 0),
                        'Total_Points': event.get('total_points', 0),
                        'Transfers': event.get('event_transfers', 0),
                        'Transfer_Cost': event.get('event_transfers_cost', 0),
                        'Bench_Points': event.get('points_on_bench', 0),
                        'Has_Data': True,
                    })

            # Điền chỗ trống cho các GW entry chưa có dữ liệu
            for gw in gw_range:
                if gw not in existing_gws:
                    results.append(_blank_row(team_id, manager, team, gw))

    if not results:
        raise FPLError("Unable to fetch points data for any entry")

    return pd.DataFrame(results)


def build_month_points_table(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int],
                             finished_gws: Optional[Set[int]] = None) -> pd.DataFrame:
    """
    Build month points table from GW data (excluding incomplete months)

    Args:
        gw_points_df: DataFrame with GW points data
        month_mapping: Dict mapping GW to month number
        finished_gws: Tập GW đã chốt điểm. Mặc định lấy từ bootstrap-static.

    Returns:
        DataFrame with monthly points aggregated
    """
    if finished_gws is None:
        finished_gws = get_finished_gws()

    # Add month column to dataframe
    gw_points_df = gw_points_df.copy()
    gw_points_df['Month'] = gw_points_df['GW'].map(month_mapping)

    # Ensure Month is integer type (where not null) to avoid comparison errors
    gw_points_df['Month'] = gw_points_df['Month'].apply(lambda x: int(x) if pd.notna(x) else x)

    # Remove GWs not in mapping
    month_df = gw_points_df.dropna(subset=['Month'])

    # Chỉ giữ tháng đã hoàn tất - căn cứ events[].finished từ FPL API thay vì
    # đoán bằng "có ai đó ghi > 0 điểm" (một GW mà cả league đều 0 điểm vẫn là
    # GW hợp lệ đã đá xong).
    complete_months = []
    for month in month_df['Month'].unique():
        month_gws = [gw for gw, m in month_mapping.items() if m == month]
        if month_gws and max(month_gws) in finished_gws:
            complete_months.append(month)

    # Filter data to include only complete months
    month_df = month_df[month_df['Month'].isin(complete_months)]
    
    if month_df.empty:
        return pd.DataFrame(columns=['Team_ID', 'Manager', 'Team', 'Total'])
    
    # Group by entry and month, sum points and transfer costs
    month_summary = month_df.groupby(['Team_ID', 'Manager', 'Team', 'Month']).agg({
        'Points': 'sum',
        'Transfers': 'sum',
        'Transfer_Cost': 'sum'
    }).reset_index()
    
    # Pivot to have column for each month
    pivot_points = month_summary.pivot_table(
        index=['Team_ID', 'Manager', 'Team'],
        columns='Month',
        values='Points',
        fill_value=0
    )
    
    pivot_transfer_costs = month_summary.pivot_table(
        index=['Team_ID', 'Manager', 'Team'],
        columns='Month',
        values='Transfer_Cost',
        fill_value=0
    )
    
    # Create column names for months
    month_cols = [f"Month_{int(col)}" for col in pivot_points.columns]
    pivot_points.columns = month_cols
    pivot_transfer_costs.columns = month_cols
    
    # Add total column: Total = Sum(Points) - Sum(Transfer_Cost)
    total_points = pivot_points.sum(axis=1)
    total_transfer_costs = pivot_transfer_costs.sum(axis=1)
    pivot_points['Total'] = total_points - total_transfer_costs
    
    # Reset index for export
    result = pivot_points.reset_index()
    
    return result


def build_month_points_table_full(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int]) -> pd.DataFrame:
    """
    Build month points table from GW data (including all weeks, even with 0 points)
    This is used for Month Points tab to show complete monthly statistics
    
    Args:
        gw_points_df: DataFrame with GW points data
        month_mapping: Dict mapping GW to month number
        
    Returns:
        DataFrame with full monthly points including transfer info
    """
    # Add month column to dataframe
    gw_points_df = gw_points_df.copy()
    gw_points_df['Month'] = gw_points_df['GW'].map(month_mapping)
    
    # Ensure Month is integer type (where not null) to avoid comparison errors
    gw_points_df['Month'] = gw_points_df['Month'].apply(lambda x: int(x) if pd.notna(x) else x)
    
    # Remove GWs not in mapping
    month_df = gw_points_df.dropna(subset=['Month'])
    
    if month_df.empty:
        return pd.DataFrame(columns=['Team_ID', 'Manager', 'Team', 'Total'])
    
    # Group by entry and month, sum points and transfer costs (including 0 points)
    month_summary = month_df.groupby(['Team_ID', 'Manager', 'Team', 'Month']).agg({
        'Points': 'sum',
        'Transfers': 'sum',
        'Transfer_Cost': 'sum'
    }).reset_index()
    
    # Pivot to have column for each month
    pivot_points = month_summary.pivot_table(
        index=['Team_ID', 'Manager', 'Team'],
        columns='Month',
        values='Points',
        fill_value=0
    )
    
    pivot_transfers = month_summary.pivot_table(
        index=['Team_ID', 'Manager', 'Team'],
        columns='Month',
        values='Transfers',
        fill_value=0
    )
    
    pivot_transfer_costs = month_summary.pivot_table(
        index=['Team_ID', 'Manager', 'Team'],
        columns='Month',
        values='Transfer_Cost',
        fill_value=0
    )
    
    # Create column names for months
    month_cols = [f"Month_{int(col)}" for col in pivot_points.columns]
    pivot_points.columns = month_cols
    pivot_transfers.columns = month_cols
    pivot_transfer_costs.columns = month_cols
    
    # Reset index for all pivots
    points_df = pivot_points.reset_index()
    transfers_df = pivot_transfers.reset_index()
    transfer_costs_df = pivot_transfer_costs.reset_index()
    
    # Merge all dataframes
    result = points_df.copy()
    
    # Add transfer data
    for month_col in month_cols:
        # Get transfer and cost data for this month
        transfer_data = transfers_df[['Team_ID', 'Manager', 'Team', month_col]]
        cost_data = transfer_costs_df[['Team_ID', 'Manager', 'Team', month_col]]
        
        # Merge transfer data
        result = result.merge(
            transfer_data.rename(columns={month_col: f"{month_col}_transfers_raw"}),
            on=['Team_ID', 'Manager', 'Team'],
            how='left'
        )
        
        # Merge cost data
        result = result.merge(
            cost_data.rename(columns={month_col: f"{month_col}_costs_raw"}),
            on=['Team_ID', 'Manager', 'Team'],
            how='left'
        )
        
        # Create formatted transfer column
        transfer_col = f"{month_col}_Transfers"
        transfers_raw_col = f"{month_col}_transfers_raw"
        costs_raw_col = f"{month_col}_costs_raw"
        
        def format_month_transfer(row):
            transfers = int(row[transfers_raw_col]) if pd.notna(row[transfers_raw_col]) else 0
            cost = int(row[costs_raw_col]) if pd.notna(row[costs_raw_col]) else 0
            if transfers == 0:
                return "-"
            elif cost == 0:
                return str(transfers)
            else:
                return f"{transfers}(-{cost})"
        
        result[transfer_col] = result.apply(format_month_transfer, axis=1)
        
        # Remove the raw columns
        result = result.drop(columns=[transfers_raw_col, costs_raw_col])
    
    # Add total column: Total = Sum(Points) - Sum(Transfer_Cost)
    total_points = points_df[month_cols].sum(axis=1)
    total_transfer_costs = transfer_costs_df[month_cols].sum(axis=1)
    result['Total'] = total_points - total_transfer_costs
    
    # Add Rank column based on Total points (descending)
    result = result.sort_values('Total', ascending=False)
    result['Rank'] = range(1, len(result) + 1)
    
    # Reorder columns to have Rank first, then interleave Month_X and Month_X_Transfers
    cols = ['Rank', 'Team_ID', 'Manager', 'Team']
    
    # Get month columns and sort them properly by extracting the numeric part
    month_point_cols = [col for col in result.columns if col.startswith('Month_') and not col.endswith('_Transfers')]
    # Sort by the numeric part after 'Month_'
    month_point_cols_sorted = sorted(month_point_cols, key=lambda x: int(x.split('_')[1]))
    
    for month_col in month_point_cols_sorted:
        cols.append(month_col)
        transfer_col = f"{month_col}_Transfers"
        if transfer_col in result.columns:
            cols.append(transfer_col)
    cols.append('Total')
    result = result[cols]
    
    return result


def build_transfer_history_table(entries_df: pd.DataFrame, bootstrap_df: pd.DataFrame, 
                                 max_entries: Optional[int] = None,
                                 progress_callback=None) -> pd.DataFrame:
    """
    Build transfer history table for all entries showing transfers by gameweek
    
    Args:
        entries_df: DataFrame with league entries
        bootstrap_df: DataFrame with player data
        max_entries: Optional limit on number of entries
        progress_callback: Optional callback function(current, total, message)
        
    Returns:
        DataFrame with transfer history
    """
    if max_entries:
        entries_df = entries_df.head(max_entries)

    results = []
    total_requests = len(entries_df)

    # Create player name mapping
    player_mapping = dict(zip(bootstrap_df['id'], bootstrap_df['web_name']))

    # --- Bước 1: lấy transfer của mọi manager (1 request/manager) ---
    transfers_by_entry = {}
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        future_to_info = {}
        for _, entry in entries_df.iterrows():
            future = executor.submit(get_entry_transfers, entry['Team_ID'])
            future_to_info[future] = (entry['Team_ID'], entry['Manager'], entry['Team'])

        completed = 0
        for future in as_completed(future_to_info):
            team_id, manager, team = future_to_info[future]
            completed += 1

            if progress_callback:
                progress = completed / total_requests
                progress_callback(completed, total_requests,
                                  f"Fetching transfer data: {completed}/{total_requests} ({progress:.1%})")

            try:
                data = future.result()
            except Exception as exc:
                logger.warning("Error processing transfers for entry %s: %s", team_id, exc)
                continue

            if isinstance(data, list) and data:
                transfers_by_entry[team_id] = (manager, team, data)

    # --- Bước 2: nạp bảng điểm cho các GW liên quan (1 request/GW) ---
    involved_gws = {
        t.get('event')
        for _, _, transfers in transfers_by_entry.values()
        for t in transfers
        if t.get('event')
    }
    live_points = prefetch_live_gw_points(sorted(involved_gws))

    # --- Bước 3: dựng bảng, mọi tra cứu điểm là lookup in-memory ---
    for team_id, (manager, team, transfers) in transfers_by_entry.items():
        for transfer in transfers:
            element_in = transfer.get('element_in')
            element_out = transfer.get('element_out')
            event = transfer.get('event')
            gw_points = live_points.get(event, {})

            player_in_name = player_mapping.get(element_in, f"Player_{element_in}") if element_in else "Unknown"
            player_out_name = player_mapping.get(element_out, f"Player_{element_out}") if element_out else "Unknown"

            player_in_points = gw_points.get(element_in, 0) if element_in else 0
            player_out_points = gw_points.get(element_out, 0) if element_out else 0

            results.append({
                'Team_ID': team_id,
                'Manager': manager,
                'Team': team,
                'GW': event,
                'Transfer': f"{player_in_name} ({player_in_points}) - {player_out_name} ({player_out_points})",
                'Player_In': player_in_name,
                'Player_Out': player_out_name,
                'Player_In_Points': player_in_points,
                'Player_Out_Points': player_out_points
            })

    if not results:
        # Return empty dataframe with proper columns if no transfers found
        return pd.DataFrame(columns=['Team_ID', 'Manager', 'Team', 'GW', 'Transfer', 
                                    'Player_In', 'Player_Out', 'Player_In_Points', 'Player_Out_Points'])
    
    return pd.DataFrame(results)


def get_entry_chips_optimized(entry_id: int, gw_range: List[int]) -> Dict[int, str]:
    """
    Get chip usage for an entry across multiple GWs with minimal API calls

    ``entry/{id}/history/`` trả về khoá ``chips`` liệt kê **đầy đủ** mọi chip đã
    dùng kèm GW tương ứng, nên một request là đủ cho cả mùa. Phiên bản trước gọi
    thêm ``entry/{id}/event/{gw}/picks/`` cho từng GW không có chip chỉ để xác
    nhận là... không có chip - tức ~97% số request bị lãng phí (league 50 người
    x 38 GW ≈ 1.900 request thừa).

    Args:
        entry_id: FPL team entry ID
        gw_range: List of gameweek numbers

    Returns:
        Dict mapping GW to chip name ('-' nếu GW đó không dùng chip)
    """
    gw_set = set(gw_range)
    chips: Dict[int, str] = {}

    history_data = get_entry_history(entry_id)

    if history_data is None:
        # Không lấy được history -> không khẳng định được gì, trả '-' cho tất cả.
        logger.warning("No history for entry %s, chip data unavailable", entry_id)
        return {gw: '-' for gw in gw_range}

    for chip in history_data.get('chips') or []:
        event = chip.get('event')
        if event is not None and int(event) in gw_set:
            chips[int(event)] = chip.get('name') or '-'

    # Mọi GW còn lại chắc chắn không dùng chip - không cần request nào thêm.
    for gw in gw_range:
        chips.setdefault(gw, '-')

    return chips


def build_chip_history_table(entries_df: pd.DataFrame, gw_range: List[int], 
                             max_entries: Optional[int] = None,
                             progress_callback=None) -> pd.DataFrame:
    """
    Build chip history table for all entries showing which chips were used in each GW
    
    Args:
        entries_df: DataFrame with league entries
        gw_range: List of gameweek numbers
        max_entries: Optional limit on number of entries
        progress_callback: Optional callback function(current, total, message)
        
    Returns:
        DataFrame with chip usage history
        
    Raises:
        FPLError: If unable to fetch chip data for any entry
    """
    if max_entries:
        entries_df = entries_df.head(max_entries)
    
    results = []
    total_requests = len(entries_df)
    
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        # Submit all tasks - one per entry instead of per entry per GW
        future_to_info = {}
        for _, entry in entries_df.iterrows():
            future = executor.submit(get_entry_chips_optimized, entry['Team_ID'], gw_range)
            future_to_info[future] = (entry['Team_ID'], entry['Manager'], entry['Team'])
        
        # Collect results
        completed = 0
        for future in as_completed(future_to_info):
            team_id, manager, team = future_to_info[future]
            completed += 1
            
            # Update progress
            if progress_callback:
                progress = completed / total_requests
                progress_callback(completed, total_requests,
                                f"Fetching chip data: {completed}/{total_requests} ({progress:.1%})")
            
            try:
                chips_data = future.result()
            except Exception as exc:
                logger.warning("Error processing chips for entry %s: %s", team_id, exc)
                chips_data = {}

            for gw in gw_range:
                results.append({
                    'Team_ID': team_id,
                    'Manager': manager,
                    'Team': team,
                    'GW': gw,
                    'Active_Chip': chips_data.get(gw, '-')
                })

    if not results:
        raise FPLError("Unable to fetch chip data for any entry")

    return pd.DataFrame(results)


AWARDS_COLUMNS = ['Team_ID', 'Manager', 'Team', 'Weekly_Wins', 'Monthly_Wins', 'Total_Prize_Money']


def _entry_net_points(df: pd.DataFrame) -> pd.DataFrame:
    """Điểm ròng (đã trừ phí transfer) của từng entry trên tập hàng truyền vào.

    Chỉ tính những hàng thực sự có dữ liệu (``Has_Data``) để entry tham gia
    league muộn không bị coi là "được 0 điểm".
    """
    if 'Has_Data' in df.columns:
        df = df[df['Has_Data'].fillna(False).astype(bool)]

    if df.empty:
        return pd.DataFrame(columns=['Team_ID', 'Manager', 'Team', 'NPOINTS'])

    agg = {'Points': 'sum'}
    if 'Transfer_Cost' in df.columns:
        agg['Transfer_Cost'] = 'sum'

    totals = df.groupby(['Team_ID', 'Manager', 'Team'], as_index=False).agg(agg)
    if 'Transfer_Cost' not in totals.columns:
        totals['Transfer_Cost'] = 0

    totals['NPOINTS'] = totals['Points'] - totals['Transfer_Cost']
    return totals[['Team_ID', 'Manager', 'Team', 'NPOINTS']]


def _winner_team_ids(totals: pd.DataFrame) -> List:
    """Team_ID của (các) entry dẫn đầu; nhiều phần tử nếu hoà."""
    if totals.empty:
        return []
    best = totals['NPOINTS'].max()
    return totals.loc[totals['NPOINTS'] == best, 'Team_ID'].tolist()


def _month_ranges(month_mapping: Dict[int, int]) -> Dict[int, tuple]:
    """{month: (gw_đầu, gw_cuối)}"""
    ranges: Dict[int, tuple] = {}
    for gw, month in month_mapping.items():
        low, high = ranges.get(month, (gw, gw))
        ranges[month] = (min(low, gw), max(high, gw))
    return ranges


def tally_awards(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int],
                 finished_gws: Optional[Set[int]] = None) -> pd.DataFrame:
    """Đếm giải tuần/tháng và tiền thưởng cho từng entry.

    Đây là nguồn tính giải duy nhất cho cả ``calculate_awards_statistics`` và
    ``build_awards_leaderboard``. Trước đây hai hàm này tính theo hai cách khác
    nhau (một dùng ``Points`` thô, một dùng điểm ròng sau phí transfer) và
    ``build_awards_leaderboard`` còn cộng dồn theo **tên manager** nên hai người
    trùng tên hiển thị sẽ cùng được cộng giải.

    Chỉ GW đã chốt điểm và tháng đã đá hết mới được tính.
    """
    if finished_gws is None:
        finished_gws = get_finished_gws()

    tally = gw_points_df[['Team_ID', 'Manager', 'Team']].drop_duplicates('Team_ID').copy()
    tally['Weekly_Wins'] = 0
    tally['Monthly_Wins'] = 0
    tally['Weekly_Prize_Money'] = 0.0
    tally['Monthly_Prize_Money'] = 0.0
    tally = tally.set_index('Team_ID')

    # Giải tuần - chỉ các GW đã chốt điểm
    for gw in sorted(gw_points_df['GW'].unique()):
        if gw not in finished_gws:
            continue
        winners = _winner_team_ids(_entry_net_points(gw_points_df[gw_points_df['GW'] == gw]))
        if winners:
            tally.loc[winners, 'Weekly_Wins'] += 1
            tally.loc[winners, 'Weekly_Prize_Money'] += config.WEEKLY_PRIZE / len(winners)

    # Giải tháng - chỉ các tháng đã đá hết GW cuối
    ranges = _month_ranges(month_mapping)
    for month in sorted(set(month_mapping.values())):
        if ranges[month][1] not in finished_gws:
            continue
        month_gws = [gw for gw, m in month_mapping.items() if m == month]
        month_rows = gw_points_df[gw_points_df['GW'].isin(month_gws)]
        winners = _winner_team_ids(_entry_net_points(month_rows))
        if winners:
            tally.loc[winners, 'Monthly_Wins'] += 1
            tally.loc[winners, 'Monthly_Prize_Money'] += config.MONTHLY_PRIZE / len(winners)

    tally = tally.reset_index()
    tally['Total_Awards'] = tally['Weekly_Wins'] + tally['Monthly_Wins']
    tally['Total_Prize_Money'] = tally['Weekly_Prize_Money'] + tally['Monthly_Prize_Money']
    return tally


def calculate_awards_statistics(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int],
                                finished_gws: Optional[Set[int]] = None) -> pd.DataFrame:
    """
    Calculate awards statistics for all managers

    Args:
        gw_points_df: GW points DataFrame with columns [Team_ID, Manager, Team, GW, Points]
        month_mapping: Dict mapping GW number to month number
        finished_gws: Tập GW đã chốt điểm. Mặc định lấy từ bootstrap-static.

    Returns:
        DataFrame with columns [Team_ID, Manager, Team, Weekly_Wins, Monthly_Wins, Total_Prize_Money]
    """
    if gw_points_df.empty:
        return pd.DataFrame(columns=AWARDS_COLUMNS)

    return tally_awards(gw_points_df, month_mapping, finished_gws)[AWARDS_COLUMNS]


def create_ranking_table(data_df: pd.DataFrame, score_column: str) -> pd.DataFrame:
    """
    Create ranking table with medals for top 3

    Args:
        data_df: DataFrame with data to rank
        score_column: Column name to use for ranking

    Returns:
        DataFrame with Medal, Rank columns added
    """
    # Sort by score descending
    ranked_df = data_df.sort_values(score_column, ascending=False).reset_index(drop=True)

    # Create ranking with ties handling
    ranked_df['Rank'] = 1
    current_rank = 1

    for i in range(1, len(ranked_df)):
        prev_score = ranked_df.loc[i-1, score_column]
        curr_score = ranked_df.loc[i, score_column]

        if curr_score != prev_score:
            current_rank = i + 1

        ranked_df.loc[i, 'Rank'] = current_rank

    # Add medal column
    def get_medal(rank):
        if rank == 1:
            return "🥇"
        elif rank == 2:
            return "🥈"
        elif rank == 3:
            return "🥉"
        return ""

    ranked_df['Medal'] = ranked_df['Rank'].apply(get_medal)

    # Reorder columns to put Medal and Rank first
    cols = ['Medal', 'Rank'] + [col for col in ranked_df.columns if col not in ['Medal', 'Rank']]
    ranked_df = ranked_df[cols]

    return ranked_df


def build_weekly_ranking(gw_points_df: pd.DataFrame, selected_gw: int) -> pd.DataFrame:
    """
    Build weekly ranking for selected GW

    Args:
        gw_points_df: GW points DataFrame
        selected_gw: Selected gameweek number

    Returns:
        DataFrame with weekly ranking
    """
    # Filter for selected GW
    gw_data = gw_points_df[gw_points_df['GW'] == selected_gw].copy()

    if gw_data.empty:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Points', 'Transfers', 'NPOINTS'])

    # Chỉ loại những entry KHÔNG có dữ liệu cho GW này (tham gia league muộn).
    # Manager thực sự được 0 điểm vẫn phải nằm trong bảng và xếp cuối, thay vì
    # bị lọc mất như cách cũ (``Points > 0``).
    if 'Has_Data' in gw_data.columns:
        gw_data = gw_data[gw_data['Has_Data'].fillna(False).astype(bool)]

    if gw_data.empty:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Points', 'Transfers', 'NPOINTS'])

    # Select relevant columns
    if 'Transfer_Cost' in gw_data.columns:
        ranking_data = gw_data[['Manager', 'Team', 'Points', 'Transfers', 'Transfer_Cost']].copy()
    else:
        ranking_data = gw_data[['Manager', 'Team', 'Points', 'Transfers']].copy()
        ranking_data['Transfer_Cost'] = 0

    # Format Transfers column: transfers(-cost)
    def format_transfer(row):
        transfers = int(row['Transfers'])
        cost = int(row['Transfer_Cost'])
        if transfers == 0:
            return "-"
        elif cost == 0:
            return str(transfers)
        else:
            return f"{transfers}(-{cost})"

    ranking_data['Transfers'] = ranking_data.apply(format_transfer, axis=1)

    # Add NPOINTS column: Points - Transfer_Cost
    ranking_data['NPOINTS'] = ranking_data['Points'] - ranking_data['Transfer_Cost']

    # Remove Transfer_Cost column
    ranking_data = ranking_data.drop(columns=['Transfer_Cost'])

    # Create ranking table
    ranked_df = create_ranking_table(ranking_data, 'NPOINTS')

    return ranked_df


def build_monthly_ranking(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int], selected_month: int) -> pd.DataFrame:
    """
    Build monthly ranking for selected month

    Args:
        gw_points_df: GW points DataFrame
        month_mapping: Dict mapping GW to month
        selected_month: Selected month number

    Returns:
        DataFrame with monthly ranking
    """
    if gw_points_df.empty:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Points', 'Transfers', 'NPOINTS'])

    # Filter GWs for selected month
    month_gws = [gw for gw, month in month_mapping.items() if month == selected_month]

    if not month_gws:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Points', 'Transfers', 'NPOINTS'])

    # Filter data for selected month GWs
    month_data = gw_points_df[gw_points_df['GW'].isin(month_gws)].copy()

    # Bỏ các hàng không có dữ liệu, nhất quán với build_weekly_ranking
    if 'Has_Data' in month_data.columns:
        month_data = month_data[month_data['Has_Data'].fillna(False).astype(bool)]

    if month_data.empty:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Points', 'Transfers', 'NPOINTS'])

    # Group by team and sum points, transfers, and transfer costs
    agg_dict = {
        'Points': 'sum',
        'Transfers': 'sum'
    }

    if 'Transfer_Cost' in month_data.columns:
        agg_dict['Transfer_Cost'] = 'sum'

    monthly_summary = month_data.groupby(['Team_ID', 'Manager', 'Team']).agg(agg_dict).reset_index()

    if 'Transfer_Cost' not in monthly_summary.columns:
        monthly_summary['Transfer_Cost'] = 0

    # Format Transfers column
    def format_monthly_transfer(row):
        transfers = int(row['Transfers'])
        cost = int(row['Transfer_Cost'])
        if transfers == 0:
            return "-"
        elif cost == 0:
            return str(transfers)
        else:
            return f"{transfers}(-{cost})"

    monthly_summary['Transfers'] = monthly_summary.apply(format_monthly_transfer, axis=1)

    # Add NPOINTS column: Points - Transfer_Cost
    monthly_summary['NPOINTS'] = monthly_summary['Points'] - monthly_summary['Transfer_Cost']

    # Select relevant columns for ranking
    ranking_data = monthly_summary[['Manager', 'Team', 'Points', 'Transfers', 'NPOINTS']].copy()

    # Create ranking table
    ranked_df = create_ranking_table(ranking_data, 'NPOINTS')

    return ranked_df


def build_awards_summary_table(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int],
                               finished_gws: Optional[Set[int]] = None) -> pd.DataFrame:
    """
    Build awards summary table showing GW, Weekly Winner, and Monthly Winner

    Args:
        gw_points_df: GW points DataFrame
        month_mapping: Dict mapping GW to month
        finished_gws: Tập GW đã chốt điểm. Mặc định lấy từ bootstrap-static.

    Returns:
        DataFrame with columns [GW, Weekly_Winner, Month, Monthly_Winner]
    """
    if gw_points_df.empty:
        return pd.DataFrame(columns=['GW', 'Weekly_Winner', 'Month', 'Monthly_Winner'])

    if finished_gws is None:
        finished_gws = get_finished_gws()

    name_by_id = (
        gw_points_df[['Team_ID', 'Manager']]
        .drop_duplicates('Team_ID')
        .set_index('Team_ID')['Manager']
        .to_dict()
    )

    def _names(team_ids) -> str:
        """Tên (các) người thắng, nối bằng ' & ' khi hoà."""
        return " & ".join(name_by_id.get(tid, str(tid)) for tid in team_ids) if team_ids else "-"

    # Chỉ chốt người thắng cho GW đã kết thúc - tránh công bố "người thắng"
    # tạm thời trong lúc vòng đấu còn đang diễn ra.
    results = []
    for gw in sorted(gw_points_df['GW'].unique()):
        if gw in finished_gws:
            winners = _winner_team_ids(_entry_net_points(gw_points_df[gw_points_df['GW'] == gw]))
        else:
            winners = []

        results.append({
            'GW': gw,
            'Weekly_Winner': _names(winners),
            'Month': month_mapping.get(gw, 1),
        })

    awards_summary_df = pd.DataFrame(results)

    # Người thắng tháng - chỉ tháng đã đá hết GW cuối
    monthly_winners = {}
    ranges = _month_ranges(month_mapping)
    for month in sorted(set(month_mapping.values())):
        if ranges[month][1] not in finished_gws:
            continue
        month_gws = [gw for gw, m in month_mapping.items() if m == month]
        winners = _winner_team_ids(_entry_net_points(gw_points_df[gw_points_df['GW'].isin(month_gws)]))
        if winners:
            monthly_winners[month] = _names(winners)

    awards_summary_df['Monthly_Winner'] = awards_summary_df['Month'].map(monthly_winners).fillna("")

    return awards_summary_df


def build_awards_leaderboard(gw_points_df: pd.DataFrame, month_mapping: Dict[int, int],
                             finished_gws: Optional[Set[int]] = None) -> pd.DataFrame:
    """
    Build awards leaderboard with rankings, wins count, and prize money

    Args:
        gw_points_df: GW points DataFrame
        month_mapping: Dict mapping GW to month
        finished_gws: Tập GW đã chốt điểm. Mặc định lấy từ bootstrap-static.

    Returns:
        DataFrame with awards leaderboard
    """
    if gw_points_df.empty:
        return pd.DataFrame(columns=['Medal', 'Rank', 'Manager', 'Team', 'Weekly_Wins', 'Monthly_Wins', 'Total_Awards', 'Prize_Money'])

    # Cộng dồn giải theo Team_ID (xem tally_awards) - trước đây cộng theo tên
    # manager nên hai người trùng tên hiển thị đều được cộng cùng một giải.
    awards_df = tally_awards(gw_points_df, month_mapping, finished_gws)

    # Sort by total prize money
    awards_df = awards_df.sort_values(
        ['Total_Prize_Money', 'Total_Awards', 'Weekly_Wins', 'Monthly_Wins'],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)

    # Add rank
    awards_df['Rank'] = range(1, len(awards_df) + 1)

    # Add medal
    def get_award_medal(rank):
        if rank == 1:
            return "🏆"
        elif rank == 2:
            return "🥈"
        elif rank == 3:
            return "🥉"
        return ""

    awards_df['Medal'] = awards_df['Rank'].apply(get_award_medal)

    # Add emotion icon
    def get_emotion_icon(prize_money):
        if prize_money >= 1000000:
            return "😄"
        elif prize_money > 0:
            return "😢"
        return "😭"

    awards_df['Emotion'] = awards_df['Total_Prize_Money'].apply(get_emotion_icon)
    awards_df['Manager_Display'] = awards_df['Manager'] + ' ' + awards_df['Emotion']

    # Format prize money
    def format_prize_money(amount):
        if amount >= 1000000:
            return f"₫{amount/1000000:.1f}M"
        elif amount >= 1000:
            return f"₫{amount/1000:.0f}K"
        return f"₫{amount:.0f}"

    awards_df['Prize_Money'] = awards_df['Total_Prize_Money'].apply(format_prize_money)

    # Select columns
    result = awards_df[['Medal', 'Rank', 'Manager_Display', 'Team', 'Weekly_Wins', 'Monthly_Wins', 'Total_Awards', 'Prize_Money']].copy()
    result = result.rename(columns={'Manager_Display': 'Manager'})

    return result


FUN_STATS_COLUMNS = ['GW', 'Best_Captain', 'Worst_Captain', 'Best_Bench', 'Best_Transfer', 'Worst_Transfer']


def build_fun_stats_table(entries_df: pd.DataFrame, gw_range: List[int], bootstrap_df: pd.DataFrame,
                         max_entries: Optional[int] = None, progress_callback=None) -> pd.DataFrame:
    """
    Build fun statistics table showing best/worst captains, best bench, and best/worst transfers for each GW

    Cấu trúc lại thành các pha tách bạch để cắt số request:

    * ``get_entry_transfers`` gọi **một lần mỗi manager** thay vì lặp lại trong
      từng GW (trước là N x G lần).
    * Điểm cầu thủ lấy từ ``event/{gw}/live/`` - 1 request/GW thay vì 1 request
      cho từng cầu thủ.
    * Bỏ qua các GW chưa diễn ra, thay vì bắn N request/GW chỉ để nhận 404.
    """
    if max_entries:
        entries_df = entries_df.head(max_entries)

    # Create player name mapping
    player_mapping = dict(zip(bootstrap_df['id'], bootstrap_df['web_name']))

    finished_gws = get_finished_gws()
    current_gw = get_current_gw()
    target_gws = [gw for gw in sorted(set(gw_range)) if gw in finished_gws or gw == current_gw]

    entries = [
        (entry['Team_ID'], entry['Manager'], entry['Team'])
        for _, entry in entries_df.iterrows()
    ]

    if not target_gws or not entries:
        return pd.DataFrame(columns=FUN_STATS_COLUMNS)

    # --- Pha 1: history (điểm dự bị) + transfers, mỗi manager 1 request/loại ---
    history_data: Dict[int, Optional[Dict]] = {}
    transfers_data: Dict[int, List] = {}

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        hist_futures = {executor.submit(get_entry_history, tid): tid for tid, _, _ in entries}
        tr_futures = {executor.submit(get_entry_transfers, tid): tid for tid, _, _ in entries}

        for future in as_completed(hist_futures):
            team_id = hist_futures[future]
            try:
                history_data[team_id] = future.result()
            except Exception as exc:
                logger.warning("Error fetching history for entry %s: %s", team_id, exc)
                history_data[team_id] = None

        for future in as_completed(tr_futures):
            team_id = tr_futures[future]
            try:
                transfers_data[team_id] = future.result() or []
            except Exception as exc:
                logger.warning("Error fetching transfers for entry %s: %s", team_id, exc)
                transfers_data[team_id] = []

    # --- Pha 2: bảng điểm cầu thủ cho mọi GW cần dùng (1 request/GW) ---
    live_points = prefetch_live_gw_points(target_gws)

    # Chỉ số tra cứu nhanh
    bench_points: Dict[tuple, int] = {}
    for team_id, data in history_data.items():
        for event in (data or {}).get('current', []):
            bench_points[(team_id, event.get('event'))] = event.get('points_on_bench', 0)

    transfers_by_gw: Dict[tuple, List] = {}
    for team_id, transfer_list in transfers_data.items():
        for transfer in transfer_list or []:
            transfers_by_gw.setdefault((team_id, transfer.get('event')), []).append(transfer)

    # --- Pha 3: picks theo từng (entry, GW); GW đã chốt được cache rất dài ---
    picks_data: Dict[tuple, Optional[Dict]] = {}
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        pick_futures = {
            executor.submit(get_entry_gw_picks, team_id, gw): (team_id, gw)
            for team_id, _, _ in entries
            for gw in target_gws
        }
        total_picks = len(pick_futures)
        completed = 0
        for future in as_completed(pick_futures):
            key = pick_futures[future]
            completed += 1
            if progress_callback and completed % 10 == 0:
                progress_callback(completed, total_picks,
                                  f"Fetching picks: {completed}/{total_picks}")
            try:
                picks_data[key] = future.result()
            except Exception as exc:
                logger.warning("Error fetching picks for entry %s GW %s: %s", key[0], key[1], exc)
                picks_data[key] = None

    # --- Pha 4: tổng hợp (thuần in-memory) ---
    results = []
    for gw in target_gws:
        gw_points = live_points.get(gw, {})
        gw_data = []

        for team_id, manager, team in entries:
            data = picks_data.get((team_id, gw))
            if not data or not data.get('picks'):
                continue

            captain_pick = next((p for p in data['picks'] if p.get('is_captain')), None)
            captain_name = "Unknown"
            captain_points = 0
            if captain_pick and captain_pick.get('element'):
                element = captain_pick['element']
                captain_name = player_mapping.get(element, f"Player_{element}")
                captain_points = gw_points.get(element, 0) * captain_pick.get('multiplier', 1)

            gw_transfers = transfers_by_gw.get((team_id, gw), [])
            transfer_diff = None
            if gw_transfers:
                transfer_diff = sum(
                    gw_points.get(t.get('element_in'), 0) - gw_points.get(t.get('element_out'), 0)
                    for t in gw_transfers
                )

            gw_data.append({
                'team_id': team_id, 'manager': manager, 'team': team,
                'captain_name': captain_name, 'captain_points': captain_points,
                'bench_total_points': bench_points.get((team_id, gw), 0),
                'transfer_diff': transfer_diff,
            })

        if gw_data:
            max_captain = max(gw_data, key=lambda x: x['captain_points'])['captain_points']
            min_captain = min(gw_data, key=lambda x: x['captain_points'])['captain_points']
            max_bench = max(gw_data, key=lambda x: x['bench_total_points'])['bench_total_points']

            transfer_data = [x for x in gw_data if x['transfer_diff'] is not None]
            max_transfer = max(transfer_data, key=lambda x: x['transfer_diff'])['transfer_diff'] if transfer_data else None
            min_transfer = min(transfer_data, key=lambda x: x['transfer_diff'])['transfer_diff'] if transfer_data else None

            best_captains = [x for x in gw_data if x['captain_points'] == max_captain]
            worst_captains = [x for x in gw_data if x['captain_points'] == min_captain]
            best_benches = [x for x in gw_data if x['bench_total_points'] == max_bench]
            best_transfers = [x for x in transfer_data if x['transfer_diff'] == max_transfer] if max_transfer is not None else []
            worst_transfers = [x for x in transfer_data if x['transfer_diff'] == min_transfer] if min_transfer is not None else []

            def format_transfer(diff):
                if diff > 0: return f"+{diff}"
                return str(diff)

            results.append({
                'GW': gw,
                'Best_Captain': " | ".join([f"{x['manager']} - {x['captain_name']} ({x['captain_points']})" for x in best_captains]),
                'Worst_Captain': " | ".join([f"{x['manager']} - {x['captain_name']} ({x['captain_points']})" for x in worst_captains]),
                'Best_Bench': " | ".join([f"{x['manager']} ({x['bench_total_points']})" for x in best_benches]),
                'Best_Transfer': " | ".join([f"{x['manager']} ({format_transfer(x['transfer_diff'])})" for x in best_transfers]) if best_transfers else "-",
                'Worst_Transfer': " | ".join([f"{x['manager']} ({format_transfer(x['transfer_diff'])})" for x in worst_transfers]) if worst_transfers else "-"
            })

    # Chưa có GW nào đá xong thì đơn giản là chưa có số liệu - không phải lỗi,
    # nên trả bảng rỗng thay vì raise (trước đây route trả HTTP 500).
    if not results:
        return pd.DataFrame(columns=FUN_STATS_COLUMNS)

    return pd.DataFrame(results)


TOP_PICKS_COLUMNS = ['Rank', 'Player', 'Team_Name', 'Position', 'Times_Picked',
                     'Percent_Of_Entries', 'Total_Points']

_POSITION_NAMES = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}


def build_top_picks_table(entries_df: pd.DataFrame, gw_range: List[int], bootstrap_df: pd.DataFrame,
                          max_entries: Optional[int] = None, top_n: int = 20,
                          progress_callback=None) -> pd.DataFrame:
    """Những cầu thủ được chọn nhiều nhất trong league.

    Port từ ``compute_top_picks`` của bản Streamlit, bỏ phần phụ thuộc Streamlit
    và bổ sung:

    * chỉ duyệt các GW đã diễn ra (bản cũ bắn request cho cả GW tương lai),
    * cộng luôn tổng điểm cầu thủ ghi được trong các GW đó, lấy từ
      ``event/{gw}/live/`` nên không tốn thêm request nào cho mỗi cầu thủ.
    """
    if max_entries:
        entries_df = entries_df.head(max_entries)

    finished_gws = get_finished_gws()
    current_gw = get_current_gw()
    target_gws = [gw for gw in sorted(set(gw_range)) if gw in finished_gws or gw == current_gw]

    team_ids = list(entries_df['Team_ID'])
    if not target_gws or not team_ids:
        return pd.DataFrame(columns=TOP_PICKS_COLUMNS)

    pick_counts: Dict[int, int] = {}
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        futures = {
            executor.submit(get_entry_gw_picks, team_id, gw): (team_id, gw)
            for team_id in team_ids
            for gw in target_gws
        }
        total = len(futures)
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if progress_callback and completed % 10 == 0:
                progress_callback(completed, total, f"Analyzing picks: {completed}/{total}")
            try:
                data = future.result()
            except Exception as exc:
                logger.warning("Error fetching picks %s: %s", futures[future], exc)
                continue
            for pick in (data or {}).get('picks') or []:
                element = pick.get('element')
                if element:
                    pick_counts[element] = pick_counts.get(element, 0) + 1

    if not pick_counts:
        return pd.DataFrame(columns=TOP_PICKS_COLUMNS)

    # Tổng điểm mỗi cầu thủ trên các GW đang xét - 1 request/GW, không phải 1/cầu thủ
    live_points = prefetch_live_gw_points(target_gws)
    total_points = {
        element: sum(live_points.get(gw, {}).get(element, 0) for gw in target_gws)
        for element in pick_counts
    }

    slots = len(team_ids) * len(target_gws)
    picks_df = pd.DataFrame([
        {
            'id': element,
            'Times_Picked': count,
            'Percent_Of_Entries': round(count / slots * 100, 1) if slots else 0.0,
            'Total_Points': total_points.get(element, 0),
        }
        for element, count in pick_counts.items()
    ])

    result = picks_df.merge(
        bootstrap_df[['id', 'web_name', 'full_name', 'element_type']],
        on='id', how='left',
    )
    result['Player'] = result['web_name'].fillna('Player_' + result['id'].astype(str))
    result['Team_Name'] = result['full_name'].fillna('-')
    result['Position'] = result['element_type'].map(_POSITION_NAMES).fillna('-')

    result = result.sort_values(
        ['Times_Picked', 'Total_Points'], ascending=[False, False]
    ).head(top_n).reset_index(drop=True)
    result['Rank'] = range(1, len(result) + 1)

    return result[TOP_PICKS_COLUMNS]
