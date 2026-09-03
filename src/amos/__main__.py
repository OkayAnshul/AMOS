"""Run AMOS: `python -m amos`"""

from __future__ import annotations

import uvicorn

from amos.api.app import create_app
from amos.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, log_config=None)


if __name__ == "__main__":
    main()
