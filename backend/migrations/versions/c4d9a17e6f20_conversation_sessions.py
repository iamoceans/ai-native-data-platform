"""Persistent conversations: session scope, turns and stable message order

Implements the P1 slice of `docs/superpowers/specs/2026-10-13-askdata-conversation-skills-design.md`:

* `agent_sessions` gains the confirmed scope (`resolved_context`), the
  unconfirmed draft (`pending_clarification`), the skill binding
  (`skill_config`), an optimistic `context_version` and archival.
* `conversation_turns` is the lifecycle of one accepted user input, with the
  partial unique index that enforces "at most one active turn per session".
* `agent_messages` gains the session-scoped `sequence` (allocated under the
  session row lock), the `turn_id` link and a content schema version; existing
  rows are backfilled in `(created_at, id)` order so the transcript keeps its
  current order and becomes gap-free.
* `analysis_tasks.conversation_turn_id` links the turn to the one analysis it
  may create.
* `task_queue.kind` accepts `conversation` for the orchestration worker.

Downgrade restores the previous shape; conversation data (turns, scopes, skill
bindings) is not preserved by it, which is why the design does not promise a
lossless rollback.

Revision ID: c4d9a17e6f20
Revises: e9c5b2310d42
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4d9a17e6f20"
down_revision = "e9c5b2310d42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- agent_sessions: confirmed scope, draft, skill binding, versioning ----
    op.add_column(
        "agent_sessions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.add_column("agent_sessions", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column(
        "agent_sessions",
        sa.Column("context_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("resolved_context", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "agent_sessions", sa.Column("pending_clarification", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.add_column(
        "agent_sessions",
        sa.Column("skill_config", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_sessions_owner_time", "agent_sessions", ["user_id", "updated_at"])

    # --- conversation_turns ---------------------------------------------------
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_message_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("base_context_version", sa.Integer(), nullable=False),
        sa.Column("context_before", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("context_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("skill_snapshot", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED','RESOLVING','WAITING_INPUT','ANALYZING','RESPONDING',"
            "'COMPLETED','FAILED','CANCELLED')",
            name="ck_conversation_turn_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_message_id"], ["agent_messages.id"]),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["agent_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="conversation_turns_session_idempotency_key"
        ),
    )
    op.create_index(
        "ix_conversation_turns_session", "conversation_turns", ["session_id", "created_at", "id"]
    )
    # At most one active turn per session, enforced by the database.
    op.create_index(
        "uq_conversation_turns_active",
        "conversation_turns",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','RESOLVING','ANALYZING','RESPONDING')"),
    )

    # --- agent_messages: turn link + session-scoped order ---------------------
    op.add_column("agent_messages", sa.Column("turn_id", sa.Uuid(), nullable=True))
    op.add_column(
        "agent_messages",
        sa.Column("sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "agent_messages",
        sa.Column("content_schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_foreign_key(
        "agent_messages_turn_id_fkey", "agent_messages", "conversation_turns",
        ["turn_id"], ["id"],
    )
    # Backfill: existing rows keep their current (created_at, id) order and gain
    # a gap-free sequence; messages written before this migration are marked
    # schema version 0 so the reader can convert them.
    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   row_number() OVER (PARTITION BY session_id ORDER BY created_at, id) AS seq
            FROM agent_messages
        )
        UPDATE agent_messages m
           SET sequence = numbered.seq,
               content_schema_version = 0
          FROM numbered
         WHERE m.id = numbered.id
        """
    )
    op.create_unique_constraint(
        "agent_messages_session_sequence_key", "agent_messages", ["session_id", "sequence"]
    )

    # --- analysis_tasks: one analysis per turn --------------------------------
    op.add_column("analysis_tasks", sa.Column("conversation_turn_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "analysis_tasks_conversation_turn_id_fkey", "analysis_tasks", "conversation_turns",
        ["conversation_turn_id"], ["id"],
    )
    op.create_index(
        "uq_analysis_conversation_turn",
        "analysis_tasks",
        ["conversation_turn_id"],
        unique=True,
        postgresql_where=sa.text("conversation_turn_id IS NOT NULL"),
    )

    # --- task_queue: the conversation orchestration kind ----------------------
    op.drop_constraint("task_queue_kind_check", "task_queue", type_="check")
    op.create_check_constraint(
        "task_queue_kind_check", "task_queue",
        "kind IN ('query','analysis','ingestion','conversation')",
    )


def downgrade() -> None:
    op.drop_constraint("task_queue_kind_check", "task_queue", type_="check")
    op.create_check_constraint(
        "task_queue_kind_check", "task_queue", "kind IN ('query','analysis','ingestion')"
    )
    op.drop_index("uq_analysis_conversation_turn", table_name="analysis_tasks")
    op.drop_constraint("analysis_tasks_conversation_turn_id_fkey", "analysis_tasks", type_="foreignkey")
    op.drop_column("analysis_tasks", "conversation_turn_id")
    op.drop_constraint("agent_messages_session_sequence_key", "agent_messages", type_="unique")
    op.drop_constraint("agent_messages_turn_id_fkey", "agent_messages", type_="foreignkey")
    op.drop_column("agent_messages", "content_schema_version")
    op.drop_column("agent_messages", "sequence")
    op.drop_column("agent_messages", "turn_id")
    op.drop_index("uq_conversation_turns_active", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_session", table_name="conversation_turns")
    op.drop_table("conversation_turns")
    op.drop_index("ix_sessions_owner_time", table_name="agent_sessions")
    op.drop_column("agent_sessions", "skill_config")
    op.drop_column("agent_sessions", "pending_clarification")
    op.drop_column("agent_sessions", "resolved_context")
    op.drop_column("agent_sessions", "context_version")
    op.drop_column("agent_sessions", "archived_at")
    op.drop_column("agent_sessions", "updated_at")
