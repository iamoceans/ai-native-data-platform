"""business memory

The platform learns from its own governed analyses: every completed analysis can
contribute number-free business statements (which segments matter, which caveats
hold, what the user keeps asking about) that later planning reads back as
advisory context. Nothing here carries a figure - the deterministic kernel stays
the only source of numbers - and every row points at the analysis and the
calculation artifact it was derived from.

This is an explicit extension beyond the specification's frozen DDL list, which
has no long-term knowledge store.

Revision ID: a3f8c1d27b90
Revises: 7c1d5a9e4b02
Create Date: 2026-09-28 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3f8c1d27b90"
down_revision = "7c1d5a9e4b02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_memory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "scope_datasets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "scope_dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="proposed", nullable=False),
        sa.Column("source", sa.Text(), server_default="model", nullable=False),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("calculation_id", sa.Uuid(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("seen_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("reuse_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('segment','caveat','definition','follow_up')",
            name="ck_business_memory_kind",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','confirmed','rejected')",
            name="ck_business_memory_status",
        ),
        sa.CheckConstraint("source IN ('model','curator')", name="ck_business_memory_source"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analysis_tasks.id"]),
        sa.ForeignKeyConstraint(["calculation_id"], ["analysis_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="business_memory_dedupe_key_key"),
    )
    op.create_index(
        "ix_business_memory_metric", "business_memory", ["metric_key", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_business_memory_metric", table_name="business_memory")
    op.drop_table("business_memory")
