"""Account analysis defaults and shared authentication failure limits."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "f6a12c83d095"
down_revision = "e4b8a1c79d20"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("analysis_defaults", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_table("auth_rate_limits", sa.Column("key",sa.Text(),primary_key=True),
                    sa.Column("failures",sa.Integer(),nullable=False),
                    sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_auth_rate_limits_expires_at","auth_rate_limits",["expires_at"])

def downgrade():
    op.drop_index("ix_auth_rate_limits_expires_at","auth_rate_limits")
    op.drop_table("auth_rate_limits")
    op.drop_column("users","analysis_defaults")
