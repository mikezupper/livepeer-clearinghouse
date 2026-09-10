"""Canonical email parsing shared by every trusted boundary."""

from email_validator import EmailNotValidError, validate_email


def normalize_email(value: str) -> str:
    """Return the single canonical form used for identity subjects."""
    try:
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError as error:
        raise ValueError("invalid email address") from error
    return result.normalized.lower()
