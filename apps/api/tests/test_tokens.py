import unittest
from datetime import datetime, timedelta

from visit_agent.application.tokens import AvailabilityTokenService, hash_token
from visit_agent.domain.models import UTC
from visit_agent.infrastructure.db.repository import InMemoryRepository


class AvailabilityTokenTests(unittest.TestCase):
    def test_issue_stores_only_hash_and_validates_requirement(self):
        repo = InMemoryRepository()
        service = AvailabilityTokenService(repo)
        raw = service.issue("req-1")
        self.assertNotIn(raw, repo.availability_tokens)
        self.assertIn(hash_token(raw), repo.availability_tokens)
        self.assertIsNotNone(service.validate(raw, requirement_id="req-1"))
        self.assertIsNone(service.validate(raw, requirement_id="other"))

    def test_revoke_and_expiry_block_validation(self):
        repo = InMemoryRepository()
        service = AvailabilityTokenService(repo)
        raw = service.issue("req-1")
        self.assertTrue(service.revoke(raw))
        self.assertIsNone(service.validate(raw, requirement_id="req-1"))

        expired = service.issue("req-2")
        repo.availability_tokens[hash_token(expired)].expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        self.assertIsNone(service.validate(expired, requirement_id="req-2"))

    def test_mark_submitted_records_timestamp(self):
        repo = InMemoryRepository()
        service = AvailabilityTokenService(repo)
        raw = service.issue("req-1")
        service.mark_submitted(raw)
        self.assertIsNotNone(repo.availability_tokens[hash_token(raw)].submitted_at)
        self.assertIsNone(service.validate(raw, requirement_id="req-1"))


if __name__ == "__main__":
    unittest.main()
