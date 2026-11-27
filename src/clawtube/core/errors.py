class ClawtubeError(Exception):
    """Base error for all clawtube failures."""


class NoCaptionsAvailable(ClawtubeError):
    """Raised when every transcript source has been exhausted."""


class VideoUnavailable(ClawtubeError):
    """Video is private, deleted, age-restricted, or region-locked."""


class RateLimited(ClawtubeError):
    """Every source failed because YouTube is throttling/blocking us right
    now (HTTP 429, or an IP-block from the timedtext API) - not because the
    video itself has no captions. Worth retrying after a cooldown instead
    of giving up on the video permanently."""


class WhisperFailed(ClawtubeError):
    """The optional local Whisper fallback could not produce a transcript;
    the message says why (missing dependency, audio download blocked, no
    speech recognised) so it can be shown instead of a bare "failed"."""


class BotCheckRequired(ClawtubeError):
    """YouTube answered with "Sign in to confirm you're not a bot". Waiting
    rarely helps; passing browser cookies (--cookies-from-browser) does."""
