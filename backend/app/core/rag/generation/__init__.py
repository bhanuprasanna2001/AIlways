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
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_generator: Generator | None = None


def get_generator() -> Generator:
    """Return the shared generator instance.

    Lazily initialised on first call.

    Returns:
        Generator: Configured generator instance.
    """
    global _generator
    if _generator is None:
        settings = get_settings()
        _generator = OpenAIGenerator(
            model=settings.OPENAI_QUERY_MODEL,
            temperature=settings.RAG_GENERATION_TEMPERATURE,
            api_key=settings.OPENAI_API_KEY,
        )
    return _generator


async def generate_answer(
    query: str,
    results: list,
) -> AnswerResult:
    """Convenience function — generates a grounded answer.

    Delegates to ``get_generator().generate(query, results)``.

    Args:
        query: The user's question.
        results: Search results from the retrieval module.

    Returns:
        AnswerResult: Structured answer with citations and confidence.
    """
    gen = get_generator()
    return await gen.generate(query, results)


__all__ = [
    "Citation",
    "AnswerResult",
    "Generator",
    "OpenAIGenerator",
    "get_generator",
    "generate_answer",
]
