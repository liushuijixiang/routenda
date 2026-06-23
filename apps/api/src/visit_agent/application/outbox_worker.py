from __future__ import annotations

import signal
import time

from visit_agent.application.outbox import OutboxWorker, SMTPEmailSender
from visit_agent.config import settings
from visit_agent.infrastructure.db.sqlalchemy_repository import SQLAlchemyRepository


def main() -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for the outbox worker")
    repo = SQLAlchemyRepository(settings.database_url, seed_if_empty=False)
    worker = OutboxWorker(
        repo,
        SMTPEmailSender(settings.smtp_host, settings.smtp_port, settings.smtp_from),
        batch_size=settings.outbox_batch_size,
    )
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            processed = worker.run_once()
            if processed == 0:
                time.sleep(settings.outbox_poll_seconds)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
