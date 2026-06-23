from sqlalchemy import select

from visit_agent.infrastructure.db import sqlalchemy_repository as legacy
from visit_agent.infrastructure.db.ordered_sqlalchemy_repository import (
    OrderedSQLAlchemyRepository,
)


def test_demo_seed_persists_fk_parents_before_children(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'seed.db'}"

    repo = OrderedSQLAlchemyRepository(database_url)

    with repo.session_factory() as session:
        contact_ids = set(session.scalars(select(legacy.ContactRow.id)))
        assignment_contact_ids = set(
            session.scalars(select(legacy.ContactAssignmentRow.contact_id))
        )
        supplier_ids = set(session.scalars(select(legacy.SupplierRow.id)))
        assignment_supplier_ids = set(
            session.scalars(select(legacy.ContactAssignmentRow.supplier_id))
        )
        site_ids = set(session.scalars(select(legacy.SupplierSiteRow.id)))
        assignment_site_ids = set(
            session.scalars(select(legacy.ContactAssignmentRow.site_id))
        )

    assert assignment_contact_ids
    assert assignment_contact_ids <= contact_ids
    assert assignment_supplier_ids <= supplier_ids
    assert assignment_site_ids <= site_ids
    assert repo.data_quality_issues
    assert repo.audit
