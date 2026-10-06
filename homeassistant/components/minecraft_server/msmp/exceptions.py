"""Exceptions for the MSMP client."""


class MsmpError(Exception):
    """Base class for MSMP client errors."""


class MsmpConnectionError(MsmpError):
    """Raised when the connection to the management server fails."""


class MsmpAuthError(MsmpConnectionError):
    """Raised when the server rejects the secret."""


class MsmpRequestError(MsmpError):
    """Raised when the server answers a request with an error."""
