def mask_phone(phone: str) -> str:
    """Mask phone number for logging, keeping only the last 4 digits."""
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]
