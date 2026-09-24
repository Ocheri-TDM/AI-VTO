"""Add password accounts, opaque sessions, and chat ownership."""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0011_user_accounts"
down_revision = "0010_availability_records"
branch_labels = None
depends_on = None

LEGACY_NAME = "__legacy_local__"


def upgrade():
    now = sa.func.now()
    op.add_column("users", sa.Column("normalized_name", sa.String(200)))
    op.add_column("users", sa.Column("password_hash", sa.Text()))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    legacy_id = str(uuid4())
    connection = op.get_bind()
    connection.execute(
        sa.text("INSERT INTO users (id, display_name, normalized_name, password_hash, created_at, updated_at) "
                "VALUES (:id, :name, :name, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
        {"id": legacy_id, "name": LEGACY_NAME, "password": "!legacy-account-has-no-login!"},
    )
    # Preserve any pre-existing user rows without granting them password access.
    connection.execute(sa.text(
        "UPDATE users SET normalized_name = :prefix || id, password_hash = :password, "
        "updated_at = COALESCE(updated_at, created_at) WHERE normalized_name IS NULL"
    ), {"prefix": "__legacy_", "password": "!legacy-account-has-no-login!"})
    op.alter_column("users", "normalized_name", nullable=False)
    op.alter_column("users", "password_hash", nullable=False)
    op.alter_column("users", "updated_at", nullable=False)
    op.create_index("ix_users_normalized_name", "users", ["normalized_name"], unique=True)
    op.add_column("chats", sa.Column("user_id", sa.String(36)))
    connection.execute(sa.text(
        "UPDATE projects SET user_id = :legacy WHERE user_id IS NULL"
    ), {"legacy": legacy_id})
    connection.execute(sa.text(
        "UPDATE chats SET user_id = COALESCE((SELECT p.user_id FROM projects p WHERE p.id = chats.project_id), :legacy)"
    ), {"legacy": legacy_id})
    op.alter_column("chats", "user_id", nullable=False)
    op.create_foreign_key("fk_chats_user_id", "chats", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_chats_user_id", "chats", ["user_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade():
    op.drop_table("auth_sessions")
    op.drop_column("chats", "user_id")
    op.drop_index("ix_users_normalized_name", table_name="users")
    for column in ("last_login_at", "updated_at", "password_hash", "normalized_name"):
        op.drop_column("users", column)
