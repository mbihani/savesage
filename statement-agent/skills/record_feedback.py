"""Record one client field decision through workstream 3's feedback seam."""

from contracts.models import FieldFeedback
from contracts.ports import FeedbackStore


def record_feedback(feedback: FieldFeedback, store: FeedbackStore) -> None:
    raise NotImplementedError("workstream 3 owns feedback persistence")
