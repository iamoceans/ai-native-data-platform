"""Account profile and persisted locale/timezone preferences.

Revision ID: d2e6a9f18c40
Revises: b7e2f40c91a8
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2e6a9f18c40'
down_revision = 'b7e2f40c91a8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('display_name', sa.Text(), nullable=False, server_default=''))
    op.add_column('users', sa.Column('locale', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('timezone', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('settings_version', sa.Integer(), nullable=False, server_default='1'))


def downgrade():
    for column in ('settings_version', 'timezone', 'locale', 'display_name'):
        op.drop_column('users', column)
