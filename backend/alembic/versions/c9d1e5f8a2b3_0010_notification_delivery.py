"""0010_notification_delivery

Completes the notification system (SRS FR-15, TSD §22/§32):

- notifications: delivery retry handling — attempts, max_attempts,
  next_retry_at (exponential backoff), last_error, and per-channel sent
  markers (email_sent_at / push_sent_at) so a retry never re-sends a
  channel that already delivered (channel-level idempotency).
- notification_status gains SUPPRESSED: every channel was disabled by
  the user at dispatch time — terminal, honest, not a failure.
- notification_preferences: per-user channel toggles
  (email default ON, push default OFF — push is opt-in).
- push_outbox: dev/log push transport, mirroring email_outbox so push
  delivery is inspectable and testable.
"""

import sqlalchemy as sa

from alembic import op

revision = "c9d1e5f8a2b3"
down_revision = "d5a3c8e21b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "notifications",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
    )
    op.add_column("notifications", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("push_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_notifications_next_retry_at"), "notifications", ["next_retry_at"], unique=False)

    # PG allows ADD VALUE inside a transaction since v12.
    op.execute("ALTER TYPE notification_status ADD VALUE IF NOT EXISTS 'SUPPRESSED'")

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(
        op.f("ix_notification_preferences_user_id"),
        "notification_preferences",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "push_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("delivery", sa.String(16), nullable=False, server_default=sa.text("'LOG'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(op.f("ix_push_outbox_user_id"), "push_outbox", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_push_outbox_user_id"), table_name="push_outbox")
    op.drop_table("push_outbox")
    op.drop_index(op.f("ix_notification_preferences_user_id"), table_name="notification_preferences")
    op.drop_table("notification_preferences")
    op.drop_index(op.f("ix_notifications_next_retry_at"), table_name="notifications")
    # PG cannot DROP VALUE inside a transaction; leave the enum value in place.
    op.drop_column("notifications", "push_sent_at")
    op.drop_column("notifications", "email_sent_at")
    op.drop_column("notifications", "last_error")
    op.drop_column("notifications", "next_retry_at")
    op.drop_column("notifications", "max_attempts")
    op.drop_column("notifications", "attempts")
