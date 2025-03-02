from datetime import datetime, date


def random_day_by_month_year(month: int, year) -> date:
    """
    Return a random birth day within the specified month and year.
    """
    from random import randint
    from calendar import monthrange

    day = randint(1, monthrange(year, month)[1])
    return date(year, month, day)
