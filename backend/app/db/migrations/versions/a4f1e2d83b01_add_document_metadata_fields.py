"""Add document metadata fields for structured filtering and LLM enrichment.

Adds structured metadata columns to the ``documents`` table for SQL-backed
filtering and LLM-enriched retrieval (HyDE hypothetical questions, keywords,
summary).

Columns:
  - document_type: e.g. "invoice", "purchase_order", "shipping_order", "stock_report"
  - entity_id: e.g. "10248" for invoice_10248
  - order_date: parsed date from document content
  - customer_id: e.g. "VINET"
  - total_price: e.g. 440.00
  - summary: LLM-generated document summary
  - keywords: JSONB array of domain keywords
  - hypothetical_questions: JSONB array of HyDE questions
  - extracted_entities: JSONB object of structured entities

Indexes:
  - ix_documents_vault_doctype (vault_id, document_type)
  - ix_documents_vault_orderdate (vault_id, order_date)

Revision ID: a4f1e2d83b01
Revises: c839cafb8a9a
Create Date: 2025-06-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "a4f1e2d83b01"
down_revision: Union[str, Sequence[str], None] = "c839cafb8a9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add metadata columns and indexes to documents table."""
    # Core structured metadata (regex-extracted)
    op.add_column("documents", sa.Column("document_type", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("entity_id", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("order_date", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("customer_id", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("total_price", sa.Float(), nullable=True))

    # LLM-enriched metadata
    op.add_column("documents", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("keywords", JSONB(), nullable=True))
    op.add_column("documents", sa.Column("hypothetical_questions", JSONB(), nullable=True))
    op.add_column("documents", sa.Column("extracted_entities", JSONB(), nullable=True))

    # Composite indexes for common filter patterns
    op.create_index(
        "ix_documents_vault_doctype",
        "documents",
        ["vault_id", "document_type"],
    )
    op.create_index(
        "ix_documents_vault_orderdate",
        "documents",
        ["vault_id", "order_date"],
    )


def downgrade() -> None:
    """Remove metadata columns and indexes from documents table."""
    op.drop_index("ix_documents_vault_orderdate", table_name="documents")
    op.drop_index("ix_documents_vault_doctype", table_name="documents")

    op.drop_column("documents", "extracted_entities")
    op.drop_column("documents", "hypothetical_questions")
    op.drop_column("documents", "keywords")
    op.drop_column("documents", "summary")
    op.drop_column("documents", "total_price")
    op.drop_column("documents", "customer_id")
    op.drop_column("documents", "order_date")
    op.drop_column("documents", "entity_id")
    op.drop_column("documents", "document_type")
