"""A user has at most one current business-memory team; roles remain independent."""
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.orm import Team, User
from app.repositories import users

def list_teams(session: Session) -> list[Team]:
    return list(session.scalars(select(Team).order_by(Team.name, Team.id)))

def create(session: Session, name: str) -> Team | None:
    # A short advisory lock makes duplicate creation a conflict, not a 500.
    from sqlalchemy import text
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), {"name": "team:" + name})
    if session.scalar(select(Team.id).where(Team.name == name)):
        return None
    row = Team(id=uuid.uuid4(), name=name)
    session.add(row)
    session.flush()
    return row

def assign(session: Session, user_id: uuid.UUID, team_id: uuid.UUID | None) -> tuple[User, uuid.UUID | None] | None:
    user = users.get_user_for_update(session, user_id)
    if user is None or (team_id is not None and session.get(Team, team_id) is None):
        return None
    previous = user.team_id
    user.team_id = team_id
    session.flush()
    return user, previous
