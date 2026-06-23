from __future__ import annotations

import uvicorn

from visit_agent.api.app import app
from visit_agent.config import settings


def main() -> None:
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
