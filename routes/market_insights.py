"""Market Insights API.

Two endpoints, both authenticated but not tenant-scoped (the underlying
RentCast cache is global lookup data shared across orgs):

- GET /api/market-insights/areas
    Lists the configured ServiceArea rows for the dashboard's selector.

- GET /api/market-insights/<area_slug>?window=90d|180d|12m
    Returns the cached market rollup for an area, sliced to the chosen window.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required

from services.market_insights_service import (
    DEFAULT_WINDOW,
    WINDOW_TO_MONTHS,
    get_insights,
    list_service_areas,
)

market_insights_bp = Blueprint('market_insights', __name__)


@market_insights_bp.route('/api/market-insights/areas', methods=['GET'])
@login_required
def list_areas():
    return jsonify({'areas': list_service_areas()})


@market_insights_bp.route('/api/market-insights/<area_slug>', methods=['GET'])
@login_required
def get_area_insights(area_slug):
    window = request.args.get('window', DEFAULT_WINDOW)
    if window not in WINDOW_TO_MONTHS:
        window = DEFAULT_WINDOW

    result = get_insights(area_slug, window=window)
    if result is None:
        return jsonify({'error': f'Unknown service area: {area_slug}'}), 404

    if 'error' in result and 'history' not in result:
        # Cold cache + RentCast failure -- spec says return 503 with the error.
        return jsonify(result), 503

    return jsonify(result)
