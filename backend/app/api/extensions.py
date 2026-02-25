from typing import Any, Dict, cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def adapt_type_error(message: str) -> str:
    """Adapt a type error message to be more user-friendly.

    Falls back to the original message if it does not contain the expected
    substring, preventing ValueError on malformed error strings.

    Args:
        message: The original error message.

    Returns:
        The adapted error message.
    """
    try:
        return message[message.index("missing"):].replace("positional argument", "field")
    except ValueError:
        return message


def adapt_message(error: Dict[str, Any]) -> str:
    """Adapt a validation error message based on its type.

    Args:
        error: The error dictionary from Pydantic validation.

    Returns:
        The adapted error message.
    """
    msg = error.get("msg", "")
    if msg and error.get("type") == "type_error":
        return adapt_type_error(msg)
    return msg


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Custom exception handler for request validation errors.

    Args:
        _: The incoming HTTP request (not used).
        exc: The exception that was raised.
    
    Returns:
        JSONResponse: A JSON response containing the adapted error messages.
    """
    validation_error = cast(RequestValidationError, exc)
    errors = []
    for error in validation_error.errors():
        error.update({"msg": adapt_message(error)})
        if "ctx" in error:
            error["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        errors.append(error)
    return JSONResponse({"detail": errors}, status_code=422)
