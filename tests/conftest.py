import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_gw_points(rows):
    """Dựng gw_points_df từ (team_id, manager, gw, points, transfer_cost, has_data)."""
    return pd.DataFrame([
        {
            'Team_ID': team_id,
            'Manager': manager,
            'Team': f'Team {manager}',
            'GW': gw,
            'Points': points,
            'Total_Points': points,
            'Transfers': 0,
            'Transfer_Cost': cost,
            'Bench_Points': 0,
            'Has_Data': has_data,
        }
        for team_id, manager, gw, points, cost, has_data in rows
    ])


@pytest.fixture
def gw_points_df():
    """2 GW, 3 manager.

    GW1: Alice=50, Bob=60 (bị trừ 4 phí transfer), Cara=0 điểm thật.
    GW2: Alice=70, Bob=70 (hoà), Cara chưa có dữ liệu.
    """
    return make_gw_points([
        (1, 'Alice', 1, 50, 0, True),
        (2, 'Bob',   1, 60, 4, True),
        (3, 'Cara',  1, 0,  0, True),
        (1, 'Alice', 2, 70, 0, True),
        (2, 'Bob',   2, 70, 0, True),
        (3, 'Cara',  2, 0,  0, False),
    ])
