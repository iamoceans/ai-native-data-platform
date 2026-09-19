"""Charts: controlled ChartSpec artifacts (spec section 25)."""

from app.charts.spec import (  # noqa: F401
    CHART_KINDS,
    MAX_CHART_POINTS,
    ChartSpec,
    ChartValidationError,
    build_bounded_view,
    contribution_chart,
    validate_chart_data,
)
