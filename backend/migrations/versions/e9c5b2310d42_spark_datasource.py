"""Allow Spark Thrift Server datasource registrations.

Revision ID: e9c5b2310d42
Revises: a3f8c1d27b90
"""

from alembic import op

revision = "e9c5b2310d42"
down_revision = "a3f8c1d27b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("datasources_kind_check", "datasources", type_="check")
    op.create_check_constraint(
        "datasources_kind_check", "datasources",
        "kind IN ('postgres','mysql','doris','spark')",
    )


def downgrade() -> None:
    op.drop_constraint("datasources_kind_check", "datasources", type_="check")
    op.create_check_constraint(
        "datasources_kind_check", "datasources", "kind IN ('postgres','mysql','doris')",
    )
