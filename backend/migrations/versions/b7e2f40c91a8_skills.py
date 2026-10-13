"""Skill identities and their immutable versions (design 8.2).

Two tables, deliberately split:

* `skills` is the *identity* a user manages - owner, slug, enabled flag,
  archival. A builtin row has no owner (`owner_user_id IS NULL`) and is read-only
  for ordinary users.
* `skill_versions` is *content*. A row is never updated after insert: enabling,
  disabling or archiving happens on the identity, and re-importing different
  content creates the next version instead of overwriting history.

The content hash is unique per skill, so re-uploading the same package returns
the existing version rather than a duplicate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e2f40c91a8"
down_revision = "c4d9a17e6f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('builtin','upload')", name="ck_skills_source"),
        sa.CheckConstraint(
            "(source = 'builtin' AND owner_user_id IS NULL) OR "
            "(source = 'upload' AND owner_user_id IS NOT NULL)",
            name="ck_skills_owner_matches_source",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "slug", name="skills_owner_slug_key"),
    )
    # Builtin slugs occupy their own namespace: a NULL owner never participates
    # in an ordinary unique constraint, so it needs a partial index.
    op.create_index(
        "uq_skills_builtin_slug", "skills", ["slug"], unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )
    op.create_index("ix_skills_owner", "skills", ["owner_user_id", "enabled"])

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("compatibility", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("version >= 1", name="ck_skill_versions_version"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="skill_versions_skill_version_key"),
        sa.UniqueConstraint("skill_id", "content_hash", name="skill_versions_skill_hash_key"),
    )
    op.create_index("ix_skill_versions_skill", "skill_versions", ["skill_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_skill_versions_skill", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index("ix_skills_owner", table_name="skills")
    op.drop_index("uq_skills_builtin_slug", table_name="skills")
    op.drop_table("skills")
