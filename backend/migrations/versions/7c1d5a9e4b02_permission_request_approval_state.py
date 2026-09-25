"""permission request approval state

A request can now end in a real grant, which is a different outcome from the
mock state: ``APPROVED`` means an administrator created the actual permission
(and the policy revision was bumped), ``MOCK_APPROVED`` remains a label that
grants nothing. Keeping them apart in the schema is what lets the Permissions
screen - and the requester - tell "granted" from "pretended".

Revision ID: 7c1d5a9e4b02
Revises: 4f36847b8351
Create Date: 2026-09-25 03:20:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "7c1d5a9e4b02"
down_revision = "4f36847b8351"
branch_labels = None
depends_on = None

OLD = "status IN ('REQUESTED','MOCK_APPROVED','REJECTED')"
NEW = "status IN ('REQUESTED','MOCK_APPROVED','APPROVED','REJECTED')"


def upgrade() -> None:
    op.drop_constraint("permission_requests_status_check", "permission_requests", type_="check")
    op.create_check_constraint("permission_requests_status_check", "permission_requests", NEW)


def downgrade() -> None:
    # Rows in the new state have no meaning in the old schema; they fall back to
    # the mock state rather than being deleted.
    op.execute("UPDATE permission_requests SET status = 'MOCK_APPROVED' WHERE status = 'APPROVED'")
    op.drop_constraint("permission_requests_status_check", "permission_requests", type_="check")
    op.create_check_constraint("permission_requests_status_check", "permission_requests", OLD)
