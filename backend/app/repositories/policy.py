"""Policy-state access: the global authorization revision (spec section 8)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import PolicyState


def get_revision(session: Session) -> int:
    row = session.execute(select(PolicyState).where(PolicyState.singleton.is_(True))).scalar_one_or_none()
    if row is None:
        row = PolicyState(singleton=True, revision=1)
        session.add(row)
        session.flush()
    return int(row.revision)


def bump_revision(session: Session) -> int:
    """Increment and lock the policy revision; must run in the same tx as the
    permission change it describes (spec section 8.1)."""
    row = session.execute(
        select(PolicyState).where(PolicyState.singleton.is_(True)).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = PolicyState(singleton=True, revision=1)
        session.add(row)
        session.flush()
    row.revision = int(row.revision) + 1
    session.flush()
    return int(row.revision)
