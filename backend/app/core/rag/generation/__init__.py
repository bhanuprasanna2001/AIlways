"""Generation package — LLM-powered answer generation with citations.

Provides a ``get_generator`` factory and a convenience
``generate_answer`` function.

Usage::

    from app.core.rag.generation import generate_answer

    result = await generate_answer(query, search_results)
"""

from app.core.rag.generation.base import Citation, AnswerResult, Generator
from app.core.rag.generation.openai import OpenAIGenerator
from app.core.config import get_settings
from app.core.utils import singleton


@singleton
def get_generator() -> Generator:
    """Return the shared generator instance (lazily initialised)."""
    settings = get_settings()
    return OpenAIGenerator(
        model=settings.OPENAI_QUERY_MODEL,
        temperature=settings.RAG_GENERATION_TEMPERATURE,
        api_key=settings.OPENAI_API_KEY,
    )


async def generate_answer(query: str, results: list) -> AnswerResult:
    """Convenience wrapper — delegates to the shared generator."""
    return await get_generator().generate(query, results)


__all__ = [
    "Citation",
    "AnswerResult",
    "Generator",
    "OpenAIGenerator",
    "get_generator",
    "generate_answer",
]
