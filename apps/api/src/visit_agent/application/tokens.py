from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from visit_agent.domain.models import AvailabilityToken, UTC
from visit_agent.infrastructure.db.repository import InMemoryRepository


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AvailabilityTokenService:
    repo: InMemoryRepository
    ttl_hours: int = 72

    def issue(self, requirement_id: str) -> str:
        raw = token_urlsafe(32)
        record = AvailabilityToken(
            requirement_id=requirement_id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=self.ttl_hours),
        )
        self.repo.availability_tokens[record.token_hash] = record
        return raw

    def validate(
        self, raw_token: str, requirement_id: str | None = None
    ) -> AvailabilityToken | None:
        record = self.repo.availability_tokens.get(hash_token(raw_token))
        if not record or not record.is_active():
            return None
        if requirement_id and record.requirement_id != requirement_id:
            return None
        return record

    def mark_submitted(self, raw_token: str) -> None:
        record = self.repo.availability_tokens.get(hash_token(raw_token))
        if record:
            record.submitted_at = datetime.now(UTC)

    def revoke(self, raw_token: str) -> bool:
        record = self.repo.availability_tokens.get(hash_token(raw_token))
        if not record:
            return False
        record.revoked_at = datetime.now(UTC)
        return True
