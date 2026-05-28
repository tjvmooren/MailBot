class MailBotError(RuntimeError):
    """Base error for MailBot."""


class ConfigError(MailBotError):
    """Raised when local configuration is invalid."""


class AuthRequiredError(MailBotError):
    """Raised when Gmail auth is required before proceeding."""
