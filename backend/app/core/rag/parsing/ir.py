from pydantic import BaseModel


class ParsedTable(BaseModel):
    """A table extracted from a document."""
    content: str
    page_number: int | None = None


class DocumentMetadata(BaseModel):
    """Metadata extracted from a parsed document."""
    title: str | None = None
    author: str | None = None
    created_date: str | None = None
    page_count: int | None = None


class ParsedSection(BaseModel):
    """A section of parsed document content."""
    heading: str | None = None
    level: int = 0
    content: str = ""
    page_number: int | None = None
    tables: list[ParsedTable] = []


class ParsedDocument(BaseModel):
    """Intermediate representation of a parsed document.

    This is the output of any parser and the input to the chunker.
    Stored as JSON alongside the raw file for re-chunking without re-parsing.
    """
    doc_id: str
    sections: list[ParsedSection] = []
    metadata: DocumentMetadata = DocumentMetadata()
    raw_text: str = ""
    parse_warnings: list[str] = []
