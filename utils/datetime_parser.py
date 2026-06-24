import dateparser


def parse_datetime(text):

    dt = dateparser.parse(
        text,
        settings={
            "PREFER_DATES_FROM": "future",
        }
    )

    if not dt:
        return None

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )