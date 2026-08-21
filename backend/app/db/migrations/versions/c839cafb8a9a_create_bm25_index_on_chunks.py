"""Create BM25 index on chunks.

Revision ID: c839cafb8a9a
Revises: 0838ab6e302a
Create Date: 2026-02-24 15:41:26.480504

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c839cafb8a9a'
down_revision: Union[str, Sequence[str], None] = '0838ab6e302a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ParadeDB BM25 index on chunks.content_with_header."""
    op.execute("""
        CREATE INDEX chunks_bm25_idx
        ON chunks
        USING bm25(id, content_with_header)
        WITH (key_field = 'id')
    """)


def downgrade() -> None:
    """Drop the BM25 index."""
    op.execute("DROP INDEX IF EXISTS chunks_bm25_idx")
