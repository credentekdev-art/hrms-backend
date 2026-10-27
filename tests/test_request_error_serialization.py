import json
from datetime import date
from app.services.capacity import CapacityConflict


def test_capacity_conflict_can_be_returned_as_json():
    conflict = CapacityConflict(
        date=date(2026, 10, 19),
        concurrent_projects=4,
        total_percentage=110,
        reason="Schedule conflict",
    )
    detail = {
        "message": "This employee is not available for the requested schedule.",
        "conflicts": [{
            "date": conflict.date.isoformat(),
            "concurrent_projects": conflict.concurrent_projects,
            "total_percentage": conflict.total_percentage,
            "reason": conflict.reason,
        }],
    }
    payload = json.dumps(detail)
    assert '2026-10-19' in payload
