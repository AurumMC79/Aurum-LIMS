"""Exceptions raised by the service layer and shown to users by the UI."""


class AurumError(Exception):
    """Base class for expected, user-facing errors."""


class ValidationError(AurumError):
    """Input was rejected (bad password, duplicate username, missing reason...)."""


class PermissionDenied(AurumError):
    """The signed-in user's role does not allow this action."""


class NotFound(AurumError):
    """A requested record does not exist."""
