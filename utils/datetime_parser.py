import dateparser
from loguru import logger


def parse_datetime(text):

    try:

        dt = dateparser.parse(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": "Asia/Kolkata",
            }
        )

        if not dt:
            return None

        return dt.strftime("%Y-%m-%d %H:%M:%S")

    except Exception as e:

        logger.error(f"DateTime parsing failed (len={len(text)}): {e}")

        return None
