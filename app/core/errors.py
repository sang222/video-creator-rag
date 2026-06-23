class VCOSError(Exception):
    """Base application error."""


class NotFoundError(VCOSError):
    pass


class DatabaseUnavailableError(VCOSError):
    pass


class ConfigRegistryError(VCOSError):
    pass


class ConfigVersionConflictError(ConfigRegistryError):
    pass


class ValidationFailureError(VCOSError):
    pass


class ForbiddenError(VCOSError):
    pass


class ConflictError(VCOSError):
    pass
