class ParseError(Exception):
    """Raised when document parsing fails."""
    pass


class IngestionError(Exception):
    """Raised when the ingestion pipeline fails."""
    pass
