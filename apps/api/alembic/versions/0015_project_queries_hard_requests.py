"""Project query history and persistent hard-request cases."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_hard_requests"
down_revision = "0014_adaptive_sync"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.add_column("projects", sa.Column("active_query_id", sa.String(36), nullable=True))
    op.create_index("ix_projects_active_query_id", "projects", ["active_query_id"])
    op.create_table(
        "project_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "research_session_id", sa.String(36), sa.ForeignKey("research_sessions.id", ondelete="SET NULL")
        ),
        sa.Column("type", sa.String(20), nullable=False, server_default="STANDARD"),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_queries_project_id", "project_queries", ["project_id"])
    op.create_index("ix_project_queries_research_session_id", "project_queries", ["research_session_id"])
    op.create_index("ix_project_queries_status", "project_queries", ["status"])
    op.create_index(
        "uq_project_active_standard",
        "project_queries",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE' AND type = 'STANDARD'"),
    )
    op.execute("""
      INSERT INTO project_queries(id, project_id, research_session_id, type, query_text, status, created_at, updated_at)
      SELECT gen_random_uuid()::text, p.id, r.id, 'STANDARD', COALESCE(r.payload->'intent'->>'raw_query','Предыдущий запрос'), 'ACTIVE', r.created_at, r.updated_at
      FROM projects p JOIN chats c ON c.project_id=p.id
      JOIN LATERAL (SELECT * FROM research_sessions rr WHERE rr.chat_id=c.id ORDER BY rr.created_at DESC LIMIT 1) r ON true
    """)
    op.execute(
        "UPDATE projects p SET active_query_id=q.id FROM project_queries q WHERE q.project_id=p.id AND q.status='ACTIVE'"
    )
    op.create_table(
        "hard_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "query_id",
            sa.String(36),
            sa.ForeignKey("project_queries.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("parsed_plan", JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("target_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metrics", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hard_requests_project_id", "hard_requests", ["project_id"])
    op.create_index("ix_hard_requests_status", "hard_requests", ["status"])
    op.create_table(
        "hard_request_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "hard_request_id",
            sa.String(36),
            sa.ForeignKey("hard_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_number", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(10, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("hard_request_id", "case_number"),
    )
    op.create_index("ix_hard_request_cases_hard_request_id", "hard_request_cases", ["hard_request_id"])
    op.create_table(
        "hard_request_positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("hard_request_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position_number", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.String(64), sa.ForeignKey("product_concepts.id", ondelete="SET NULL")),
        sa.Column("category_key", sa.String(300), nullable=False),
        sa.Column("category_label", sa.String(300), nullable=False),
        sa.Column("requested_candidates", sa.Integer(), nullable=False),
        sa.UniqueConstraint("case_id", "position_number"),
    )
    op.create_index("ix_hard_request_positions_case_id", "hard_request_positions", ["case_id"])
    op.create_table(
        "hard_request_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "position_id",
            sa.String(36),
            sa.ForeignKey("hard_request_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "indexed_product_id",
            sa.String(64),
            sa.ForeignKey("indexed_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "supplier_offer_id",
            sa.String(200),
            sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("availability_tier", sa.String(1), nullable=False),
        sa.Column("score", sa.Numeric(10, 4), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("user_locked", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("position_id", "supplier_offer_id"),
    )
    for n, c in [
        ("position_id", "position_id"),
        ("indexed_product_id", "indexed_product_id"),
        ("supplier_offer_id", "supplier_offer_id"),
        ("availability_tier", "availability_tier"),
    ]:
        op.create_index(f"ix_hard_request_candidates_{n}", "hard_request_candidates", [c])


def downgrade():
    for table in (
        "hard_request_candidates",
        "hard_request_positions",
        "hard_request_cases",
        "hard_requests",
        "project_queries",
    ):
        op.drop_table(table)
    op.drop_index("ix_projects_active_query_id", table_name="projects")
    op.drop_column("projects", "active_query_id")
