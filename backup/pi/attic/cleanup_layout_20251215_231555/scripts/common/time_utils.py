from datetime import datetime
from typing import Optional

def format_ts(dt: Optional[datetime] = None) -> str:
    """
    Canonical ER1 Python timestamp:
    YYYY.MM.DD HH:MM:SS.mmm
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]

