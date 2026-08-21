"""
Main Routes
Page rendering routes
"""

from flask import render_template
from . import main_bp
import config


@main_bp.route('/')
def index():
    """Dashboard / Home page"""
    return render_template('pages/dashboard.html',
                          page_title='Dashboard',
                          config=config)


@main_bp.route('/league-members')
def league_members():
    """League members page"""
    return render_template('pages/league_members.html',
                          page_title='League Members',
                          config=config)


@main_bp.route('/gw-points')
def gw_points():
    """GW Points page"""
    return render_template('pages/gw_points.html',
                          page_title='GW Points',
                          config=config)


@main_bp.route('/month-points')
def month_points():
    """Month Points page"""
    return render_template('pages/month_points.html',
                          page_title='Month Points',
                          config=config)


@main_bp.route('/top-picks')
def top_picks():
    """Top Picks page"""
    return render_template('pages/top_picks.html',
                          page_title='Top Picks',
                          config=config)


@main_bp.route('/rankings')
def rankings():
    """Rankings page"""
    return render_template('pages/rankings.html',
                          page_title='Rankings',
                          config=config)


@main_bp.route('/awards')
def awards():
    """Awards Statistics page"""
    return render_template('pages/awards.html',
                          page_title='Awards Statistics',
                          config=config)


@main_bp.route('/chip-history')
def chip_history():
    """Chip History page"""
    return render_template('pages/chip_history.html',
                          page_title='Chip History',
                          config=config)


@main_bp.route('/fun-stats')
def fun_stats():
    """Fun Stats page"""
    return render_template('pages/fun_stats.html',
                          page_title='Fun Stats',
                          config=config)


@main_bp.route('/transfer-history')
def transfer_history():
    """Transfer History page"""
    return render_template('pages/transfer_history.html',
                          page_title='Transfer History',
                          config=config)
