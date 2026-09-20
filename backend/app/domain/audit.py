"""The audit and product-metric trail.

Events carry ids, amounts and reasons — never a session token, a model key or the private
text of a chat, a receipt or a statement.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


async def record(
    session: AsyncSession,
    event_type: str,
    *,
    request_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    subject_id: uuid.UUID | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        request_id=request_id,
        actor_id=actor_id,
        actor_role=actor_role,
        event_type=event_type,
        subject_id=subject_id,
        reason=reason,
        data=data or {},
    )
    session.add(event)
    return event
