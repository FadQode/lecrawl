"""### Create document localizations and resources.

Revision ID: 0002_document_content
Revises: 0001_sources_documents
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_document_content"
down_revision: str | None = "0001_sources_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRANSLATION_STATUSES = (
    "'not_configured', 'not_discovered', 'pending', 'complete', 'failed', 'access_blocked', 'unavailable'"
)
RESOURCE_KINDS = "'pdf', 'excel', 'word', 'csv', 'other'"
RESOURCE_STATUSES = "'pending', 'complete', 'not_applicable', 'access_blocked', 'failed', 'url_unavailable'"


def upgrade() -> None:
    """### Create `document_localizations` and `document_resources`."""
    op.create_table(
        "document_localizations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", name="fk_document_localizations_document_id_documents", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language_code", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("listing_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "language_code", name="uq_document_localizations_document_id_language_code"),
        sa.CheckConstraint(f"status IN ({TRANSLATION_STATUSES})", name="status_values"),
        sa.CheckConstraint(
            "(text_content IS NULL AND content_hash IS NULL) OR "
            "(text_content IS NOT NULL AND content_hash IS NOT NULL)",
            name="content_state",
        ),
        sa.CheckConstraint(
            "status <> 'complete' OR text_content IS NOT NULL",
            name="complete_requires_content",
        ),
    )
    op.create_index(
        "ix_document_localizations_language_code_status",
        "document_localizations",
        ["language_code", "status"],
    )

    op.create_table(
        "document_resources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", name="fk_document_resources_document_id_documents", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("character_count", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("extraction_status", sa.String(length=24), nullable=False),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("archive_url", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "identity_key", name="uq_document_resources_document_id_identity_key"),
        sa.CheckConstraint(f"resource_type IN ({RESOURCE_KINDS})", name="resource_type_values"),
        sa.CheckConstraint(
            f"extraction_status IN ({RESOURCE_STATUSES})",
            name="extraction_status_values",
        ),
        sa.CheckConstraint(
            "(extracted_text IS NULL AND content_hash IS NULL) OR "
            "(extracted_text IS NOT NULL AND content_hash IS NOT NULL)",
            name="extraction_state",
        ),
        sa.CheckConstraint(
            "extraction_status <> 'complete' OR extracted_text IS NOT NULL",
            name="complete_requires_text",
        ),
        sa.CheckConstraint(
            "original_url IS NOT NULL OR extraction_status = 'url_unavailable'",
            name="url_unavailable_state",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="file_size_non_negative",
        ),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_non_negative"),
        sa.CheckConstraint(
            "character_count IS NULL OR character_count >= 0",
            name="character_count_non_negative",
        ),
    )
    op.create_index(
        "ix_document_resources_document_id_extraction_status",
        "document_resources",
        ["document_id", "extraction_status"],
    )
    op.create_index(
        "ix_document_resources_extraction_status_updated_at",
        "document_resources",
        ["extraction_status", sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    """### Drop `document_resources` then `document_localizations`."""
    op.drop_index("ix_document_resources_extraction_status_updated_at", table_name="document_resources")
    op.drop_index("ix_document_resources_document_id_extraction_status", table_name="document_resources")
    op.drop_table("document_resources")
    op.drop_index("ix_document_localizations_language_code_status", table_name="document_localizations")
    op.drop_table("document_localizations")
