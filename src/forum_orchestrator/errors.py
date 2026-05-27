class OrchestratorError(Exception):
    pass


class AuthError(OrchestratorError):
    pass


class DeadLink(OrchestratorError):
    """The remote indicated the resource is gone (404 / 410 / explicit message)."""


class TransientError(OrchestratorError):
    """Network blip, 5xx, CF interstitial we couldn't bypass."""


class ResolverError(OrchestratorError):
    pass


class UnsupportedHost(ResolverError):
    pass
