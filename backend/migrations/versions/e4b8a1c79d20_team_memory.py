"""Team-scoped memories; existing memories become private to their source owner."""
from alembic import op
import sqlalchemy as sa
revision = "e4b8a1c79d20"
down_revision = "d2e6a9f18c40"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("teams", sa.Column("id", sa.Uuid(), primary_key=True),
                    sa.Column("name", sa.Text(), nullable=False, unique=True))
    op.add_column("users", sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id")))
    op.create_index("ix_users_team_id", "users", ["team_id"])
    op.add_column("business_memory", sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id")))
    op.add_column("business_memory", sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id")))
    op.create_index("ix_business_memory_team_id", "business_memory", ["team_id"])
    op.create_index("ix_business_memory_owner_user_id", "business_memory", ["owner_user_id"])
    op.execute("UPDATE business_memory m SET owner_user_id = a.user_id FROM analysis_tasks a WHERE m.analysis_id = a.id")

def downgrade():
    op.drop_index("ix_business_memory_owner_user_id", "business_memory")
    op.drop_index("ix_business_memory_team_id", "business_memory")
    op.drop_column("business_memory", "owner_user_id")
    op.drop_column("business_memory", "team_id")
    op.drop_index("ix_users_team_id", "users")
    op.drop_column("users", "team_id")
    op.drop_table("teams")
