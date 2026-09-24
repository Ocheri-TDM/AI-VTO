from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.models import utcnow

JsonType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class IndexedProduct(Base):
    __tablename__ = 'indexed_products'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(200), index=True)
    search_document: Mapped[str] = mapped_column(Text)
    metadata_payload: Mapped[dict] = mapped_column(JsonType, default=dict)


class ProductConcept(Base):
    __tablename__ = "product_concepts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(300))
    aliases: Mapped[list] = mapped_column(JsonType, default=list)
    evidence: Mapped[dict] = mapped_column(JsonType, default=dict)
    products_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductConceptOffer(Base):
    __tablename__ = "product_concept_offers"
    concept_id: Mapped[str] = mapped_column(
        ForeignKey("product_concepts.id", ondelete="CASCADE"), primary_key=True
    )
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="CASCADE"), primary_key=True
    )


class SupplierOffer(Base):
    __tablename__ = 'supplier_offers'
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey('indexed_products.id'), index=True)
    supplier: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JsonType)
    lifecycle: Mapped[str] = mapped_column(String(30), default='ACTIVE', server_default='ACTIVE')
    missing_runs: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    metadata_hash: Mapped[str | None] = mapped_column(String(64))
    commercial_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    research_hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    unchanged_refreshes: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    last_commercial_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogRun(Base):
    __tablename__ = 'catalog_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default='PARTIAL')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomalies: Mapped[list] = mapped_column(JsonType, default=list)


class CatalogSeen(Base):
    __tablename__ = 'catalog_seen'
    run_id: Mapped[str] = mapped_column(ForeignKey('catalog_runs.id'), primary_key=True)
    offer_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict] = mapped_column(JsonType)


class TaxonomySuggestion(Base):
    __tablename__ = 'taxonomy_suggestions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    supplier: Mapped[str] = mapped_column(String(40))
    supplier_label: Mapped[str] = mapped_column(Text)
    supplier_path: Mapped[str] = mapped_column(Text)
    samples: Mapped[list] = mapped_column(JsonType, default=list)
    suggested_mapping: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[Any] = mapped_column(Numeric(5, 4), default=0)
    status: Mapped[str] = mapped_column(String(30), default='PENDING_REVIEW')


class ProductObservation(Base):
    __tablename__ = 'product_observations'
    offer_id: Mapped[str] = mapped_column(ForeignKey('supplier_offers.id', ondelete='CASCADE'), primary_key=True)
    price_kzt: Mapped[Any | None] = mapped_column(Numeric(18, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
    incoming: Mapped[int | None] = mapped_column(Integer)
    reserved: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int | None] = mapped_column(Integer)
    incoming_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability_status: Mapped[str | None] = mapped_column(String(40))
    incoming_status: Mapped[str | None] = mapped_column(String(40))
    parser_version: Mapped[str | None] = mapped_column(String(80))
    source_availability: Mapped[dict] = mapped_column(JsonType, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default='FRESH')
    payload: Mapped[dict] = mapped_column(JsonType)


class AvailabilityObservation(Base):
    __tablename__ = 'availability_observations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    offer_id: Mapped[str] = mapped_column(ForeignKey('supplier_offers.id', ondelete='CASCADE'), index=True)
    location_code: Mapped[str | None] = mapped_column(String(100))
    location_label: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(40), index=True)
    total_quantity: Mapped[int | None] = mapped_column(Integer)
    free_quantity: Mapped[int | None] = mapped_column(Integer)
    reserved_quantity: Mapped[int | None] = mapped_column(Integer)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lead_time_min_days: Mapped[int | None] = mapped_column(Integer)
    lead_time_max_days: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    parser_version: Mapped[str] = mapped_column(String(80))
    source_label: Mapped[str] = mapped_column(Text)
    source_evidence: Mapped[dict] = mapped_column(JsonType, default=dict)
    confidence: Mapped[str] = mapped_column(String(20), default='HIGH')


class SupplierSyncState(Base):
    __tablename__ = 'supplier_sync_states'
    supplier: Mapped[str] = mapped_column(String(40), primary_key=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retry_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_refresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_discovery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_incremental_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_reconciliation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_parser_version: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(String(100))
    failures: Mapped[int] = mapped_column(Integer, default=0)


class CatalogSyncJob(Base):
    __tablename__ = 'catalog_sync_jobs'
    __table_args__ = (Index('ix_sync_queue', 'status', 'available_at', 'priority'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default='PENDING')
    priority: Mapped[int] = mapped_column(Integer, default=0)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checkpoint: Mapped[dict] = mapped_column(JsonType, default=dict)
    metrics: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(String(100))


class ResearchRecord(Base):
    __tablename__ = 'research_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey('chats.id', ondelete='CASCADE'), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ResearchJobRecord(Base):
    __tablename__ = 'research_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id', ondelete='CASCADE'), index=True)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(300))
    client_name: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Chat(Base):
    __tablename__ = "chats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    active_search_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="SET NULL")
    )
    last_intent: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    search_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchSession(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (Index("ix_search_cache_expiry", "cache_key", "expires_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    intent: Mapped[dict[str, Any]] = mapped_column(JsonType)
    state: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    suppliers: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    traces: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CachedProduct(Base):
    __tablename__ = "cached_products"
    search_session_id: Mapped[str] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SearchStateHistory(Base):
    __tablename__ = "search_state_history"
    search_session_id: Mapped[str] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    decision: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
