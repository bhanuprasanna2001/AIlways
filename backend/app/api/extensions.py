from typing import Dict, cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def adapt_type_error(message: str) -> str:
    return message[message.index("missing") :].replace("positional argument", "field")


def adapt_message(error: Dict[str, str]) -> str:
    msg = error.get("msg", "")
    if msg and error.get("type") == "type_error":
        return adapt_type_error(msg)
    return msg


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    errors = []
    for error in validation_error.errors():
        error.update({"msg": adapt_message(error)})
        if "ctx" in error:
            error["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        errors.append(error)
    return JSONResponse({"detail": errors}, status_code=422)
