class AIProviderError(RuntimeError):
    """Base class for errors safe to handle at the service boundary."""


class ProviderConfigurationError(AIProviderError):
    pass


class ProviderUnavailableError(AIProviderError):
    pass


class CircuitOpenError(ProviderUnavailableError):
    pass


class ProviderResponseError(AIProviderError):
    pass
