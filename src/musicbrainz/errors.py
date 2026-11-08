"""Exception hierarchy."""

from __future__ import annotations


class MusicBrainzError(Exception):
    """Base class for all errors raised by this library."""


class ValidationError(MusicBrainzError, ValueError):
    """Invalid arguments were passed (caught client-side, before any request is made)."""


class AuthenticationRequiredError(MusicBrainzError):
    """The operation needs credentials but the client has none configured."""


class NetworkError(MusicBrainzError):
    """The request could not be completed due to a transport-level problem."""


class ResponseError(MusicBrainzError):
    """The server answered with an error status code."""

    def __init__(self, status_code: int, message: str, *, help: str | None = None, url: str | None = None):
        self.status_code = status_code
        self.message = message
        self.help = help
        self.url = url
        super().__init__(f"HTTP {status_code}: {message}" + (f" ({url})" if url else ""))


class BadRequestError(ResponseError):
    """HTTP 400: malformed request (invalid include, bad query, ...)."""


class AuthenticationError(ResponseError):
    """HTTP 401: credentials are missing or invalid."""


class ForbiddenError(ResponseError):
    """HTTP 403: authenticated but not allowed."""


class NotFoundError(ResponseError):
    """HTTP 404: no such entity."""


class RateLimitError(ResponseError):
    """HTTP 503/429: rate limit exceeded and retries exhausted."""


class ServerError(ResponseError):
    """HTTP 5xx."""


def error_for_status(status_code: int, message: str, *, help: str | None = None, url: str | None = None) -> ResponseError:
    cls: type[ResponseError]
    match status_code:
        case 400:
            cls = BadRequestError
        case 401:
            cls = AuthenticationError
        case 403:
            cls = ForbiddenError
        case 404:
            cls = NotFoundError
        case 429 | 503:
            cls = RateLimitError
        case code if code >= 500:
            cls = ServerError
        case _:
            cls = ResponseError
    return cls(status_code, message, help=help, url=url)
