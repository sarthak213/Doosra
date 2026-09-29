"""
Tiny shared registry for cancelling an in-flight /query/stream request.

Kept in its own module (rather than inside main.py or graph.py) so both can
import it without a circular import: main.py marks a request_id cancelled
when the frontend's Stop button calls POST /query/cancel/<id>, and
graph.py's run_agent() polls is_cancelled() between rounds/tool calls.
"""

_cancelled_ids: set[str] = set()


def mark_cancelled(request_id: str) -> None:
    _cancelled_ids.add(request_id)


def is_cancelled(request_id: str | None) -> bool:
    return bool(request_id) and request_id in _cancelled_ids


def clear(request_id: str | None) -> None:
    _cancelled_ids.discard(request_id)
    _owners.pop(request_id, None)


# Who started each request, so one signed-in user can't cancel another's answer.
_owners: dict[str, str] = {}


def set_owner(request_id: str | None, user: str) -> None:
    if request_id:
        _owners[request_id] = user


def may_cancel(request_id: str, user: str) -> bool:
    return _owners.get(request_id, user) == user
