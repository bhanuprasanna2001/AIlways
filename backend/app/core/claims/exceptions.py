class ClaimDetectionError(Exception):
    """Raised when claim extraction from transcript fails."""
    pass


class ClaimVerificationError(Exception):
    """Raised when claim verification against vault context fails."""
    pass
