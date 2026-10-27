from app.models.entities import RequestStatus

ALLOWED_TRANSITIONS = {
    RequestStatus.DRAFT: {RequestStatus.SUBMITTED, RequestStatus.CANCELLED},
    RequestStatus.SUBMITTED: {RequestStatus.UNDER_REVIEW, RequestStatus.CANCELLED},
    RequestStatus.UNDER_REVIEW: {RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.CANCELLED},
    RequestStatus.APPROVED: {RequestStatus.FULFILLED, RequestStatus.CANCELLED},
    RequestStatus.FULFILLED: {RequestStatus.CLOSED},
    RequestStatus.REJECTED: set(),
    RequestStatus.CLOSED: set(),
    RequestStatus.CANCELLED: set(),
}


def can_transition(current: RequestStatus, target: RequestStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())
