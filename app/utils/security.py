"""Security helpers shared by authentication and account-recovery flows."""

import secrets


def generate_otp():
    """Return a cryptographically secure six-digit one-time passcode."""
    return str(secrets.randbelow(900_000) + 100_000)


def otp_matches(stored_otp, supplied_otp):
    """Compare OTPs without early-exit string comparison."""
    if stored_otp is None or supplied_otp is None:
        return False
    return secrets.compare_digest(str(stored_otp), str(supplied_otp))
