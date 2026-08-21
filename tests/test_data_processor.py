"""Test cho phần logic thuần của data_processor (không chạm mạng)."""

import pandas as pd

import config
from conftest import make_gw_points
from services.data_processor import (
    _entry_net_points,
    _month_ranges,
    _winner_team_ids,
    build_awards_leaderboard,
    build_awards_summary_table,
    build_monthly_ranking,
    build_weekly_ranking,
    calculate_awards_statistics,
    create_ranking_table,
    parse_month_mapping,
    tally_awards,
)

MONTH_MAPPING = {1: 1, 2: 1, 3: 2, 4: 2}


# --- parse_month_mapping ---------------------------------------------------

def test_parse_month_mapping_ranges_and_singles():
    assert parse_month_mapping("1-3,4,5-6") == {1: 1, 2: 1, 3: 1, 4: 2, 5: 3, 6: 3}


def test_parse_month_mapping_invalid_returns_empty():
    assert parse_month_mapping("khong-phai-so") == {}
    assert parse_month_mapping(None) == {}


# --- điểm ròng & người thắng ----------------------------------------------

def test_net_points_subtracts_transfer_cost(gw_points_df):
    gw1 = _entry_net_points(gw_points_df[gw_points_df['GW'] == 1])
    assert dict(zip(gw1['Team_ID'], gw1['NPOINTS'])) == {1: 50, 2: 56, 3: 0}
    assert _winner_team_ids(gw1) == [2]


def test_net_points_excludes_rows_without_data(gw_points_df):
    gw2 = _entry_net_points(gw_points_df[gw_points_df['GW'] == 2])
    # Cara không có dữ liệu GW2 nên không xuất hiện
    assert set(gw2['Team_ID']) == {1, 2}


def test_winner_team_ids_returns_all_ties(gw_points_df):
    gw2 = _entry_net_points(gw_points_df[gw_points_df['GW'] == 2])
    assert sorted(_winner_team_ids(gw2)) == [1, 2]


# --- build_weekly_ranking --------------------------------------------------

def test_weekly_ranking_keeps_genuine_zero_score(gw_points_df):
    """Manager được 0 điểm thật vẫn phải có trong bảng, xếp cuối."""
    ranking = build_weekly_ranking(gw_points_df, 1)
    assert list(ranking['Manager']) == ['Bob', 'Alice', 'Cara']
    assert list(ranking['NPOINTS']) == [56, 50, 0]


def test_weekly_ranking_drops_entries_without_data(gw_points_df):
    ranking = build_weekly_ranking(gw_points_df, 2)
    assert set(ranking['Manager']) == {'Alice', 'Bob'}


def test_weekly_ranking_unplayed_gw_is_empty():
    df = make_gw_points([(1, 'Alice', 5, 0, 0, False)])
    assert build_weekly_ranking(df, 5).empty


def test_weekly_ranking_formats_transfer_hits():
    df = make_gw_points([(1, 'Alice', 1, 60, 4, True)])
    df.loc[0, 'Transfers'] = 2
    assert build_weekly_ranking(df, 1).iloc[0]['Transfers'] == '2(-4)'


# --- ranking chung ---------------------------------------------------------

def test_create_ranking_table_shares_rank_on_ties():
    df = pd.DataFrame({'Manager': ['A', 'B', 'C'], 'Score': [10, 10, 5]})
    ranked = create_ranking_table(df, 'Score')
    assert list(ranked['Rank']) == [1, 1, 3]
    assert list(ranked['Medal']) == ['\N{FIRST PLACE MEDAL}', '\N{FIRST PLACE MEDAL}', '\N{THIRD PLACE MEDAL}']


def test_monthly_ranking_sums_across_gws(gw_points_df):
    ranking = build_monthly_ranking(gw_points_df, MONTH_MAPPING, 1)
    assert dict(zip(ranking['Manager'], ranking['NPOINTS'])) == {
        'Alice': 120, 'Bob': 126, 'Cara': 0,
    }


# --- giải thưởng -----------------------------------------------------------

def test_tally_only_counts_finished_gws(gw_points_df):
    # Chỉ GW1 đã chốt -> chỉ có giải tuần GW1, chưa có giải tháng
    tally = tally_awards(gw_points_df, MONTH_MAPPING, finished_gws={1}).set_index('Team_ID')
    assert tally.loc[2, 'Weekly_Wins'] == 1
    assert tally.loc[1, 'Weekly_Wins'] == 0
    assert tally['Monthly_Wins'].sum() == 0


def test_tally_awards_month_when_last_gw_finished(gw_points_df):
    tally = tally_awards(gw_points_df, MONTH_MAPPING, finished_gws={1, 2}).set_index('Team_ID')
    # Tháng 1 = GW1+GW2: Bob 126 > Alice 120
    assert tally.loc[2, 'Monthly_Wins'] == 1
    # GW2 hoà Alice/Bob -> Bob có 2 giải tuần, Alice 1
    assert tally.loc[1, 'Weekly_Wins'] == 1
    assert tally.loc[2, 'Weekly_Wins'] == 2


def test_tied_winners_split_prize_money(gw_points_df):
    tally = tally_awards(gw_points_df, MONTH_MAPPING, finished_gws={2}).set_index('Team_ID')
    assert tally.loc[1, 'Weekly_Prize_Money'] == config.WEEKLY_PRIZE / 2
    assert tally.loc[2, 'Weekly_Prize_Money'] == config.WEEKLY_PRIZE / 2


def test_duplicate_manager_names_are_not_conflated():
    """Hai manager trùng tên hiển thị phải được tính giải riêng biệt."""
    df = make_gw_points([
        (1, 'Nguyen Van A', 1, 90, 0, True),
        (2, 'Nguyen Van A', 1, 10, 0, True),
    ])
    tally = tally_awards(df, {1: 1}, finished_gws={1}).set_index('Team_ID')
    assert tally.loc[1, 'Weekly_Wins'] == 1
    assert tally.loc[2, 'Weekly_Wins'] == 0


def test_calculate_awards_statistics_columns(gw_points_df):
    result = calculate_awards_statistics(gw_points_df, MONTH_MAPPING, finished_gws={1})
    assert list(result.columns) == [
        'Team_ID', 'Manager', 'Team', 'Weekly_Wins', 'Monthly_Wins', 'Total_Prize_Money'
    ]


def test_awards_summary_hides_winner_for_unfinished_gw(gw_points_df):
    summary = build_awards_summary_table(gw_points_df, MONTH_MAPPING, finished_gws={1}).set_index('GW')
    assert summary.loc[1, 'Weekly_Winner'] == 'Bob'
    assert summary.loc[2, 'Weekly_Winner'] == '-'


def test_awards_summary_joins_tied_winners(gw_points_df):
    summary = build_awards_summary_table(gw_points_df, MONTH_MAPPING, finished_gws={1, 2}).set_index('GW')
    assert summary.loc[2, 'Weekly_Winner'] in ('Alice & Bob', 'Bob & Alice')


def test_awards_leaderboard_sorted_by_prize(gw_points_df):
    board = build_awards_leaderboard(gw_points_df, MONTH_MAPPING, finished_gws={1, 2})
    assert board.iloc[0]['Manager'].startswith('Bob')
    assert list(board['Rank']) == [1, 2, 3]


def test_empty_input_returns_empty_frames():
    empty = pd.DataFrame(columns=['Team_ID', 'Manager', 'Team', 'GW', 'Points'])
    assert calculate_awards_statistics(empty, MONTH_MAPPING).empty
    assert build_awards_leaderboard(empty, MONTH_MAPPING).empty


# --- helper ----------------------------------------------------------------

def test_month_ranges():
    assert _month_ranges({1: 1, 2: 1, 3: 2, 4: 2}) == {1: (1, 2), 2: (3, 4)}
