def build_header(
    doc_title: str | None,
    section_heading: str | None = None,
    page_number: int | None = None,
) -> str:
    """Build a contextual header to prepend to chunk content before embedding.

    Format: [Source: {title} | Section: {heading} | Page: {page}]

    Args:
        doc_title: The document title.
        section_heading: The section heading within the document.
        page_number: The page number.

    Returns:
        str: The formatted header string.
    """
    parts = [f"Source: {doc_title or 'Unknown'}"]

    if section_heading:
        parts.append(f"Section: {section_heading}")
    if page_number is not None:
        parts.append(f"Page: {page_number}")

    return f"[{' | '.join(parts)}]"
