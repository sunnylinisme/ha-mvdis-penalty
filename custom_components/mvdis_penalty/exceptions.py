"""Exceptions for Taiwan MVDIS Penalty."""


class MvdisError(Exception):
    """Base integration error."""


class MvdisConnectionError(MvdisError):
    """Raised when the website cannot be reached."""


class CaptchaError(MvdisError):
    """Raised when CAPTCHA recognition repeatedly fails."""


class QueryRejectedError(MvdisError):
    """Raised when the submitted identity data is rejected."""


class ResponseParseError(MvdisError):
    """Raised when the website response no longer matches known markup."""
