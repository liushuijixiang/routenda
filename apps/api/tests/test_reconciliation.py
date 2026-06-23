import csv
from io import StringIO
import unittest

from visit_agent.application.reconciliation import (
    create_supplier_change_requests,
    duplicate_supplier_candidates,
    export_suppliers_csv,
    preview_supplier_import,
)
from visit_agent.domain.models import Supplier
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


def replace_csv_value(content: str, stable_id: str, field: str, value: str) -> str:
    reader = csv.DictReader(StringIO(content))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        if row["id"] == stable_id:
            row[field] = value
        writer.writerow(row)
    return output.getvalue()


class ReconciliationTests(unittest.TestCase):
    def test_export_preview_and_apply_preserve_erp_authority(self) -> None:
        repo = seed_demo(InMemoryRepository())
        supplier = next(iter(repo.suppliers.values()))
        exported = export_suppliers_csv(repo)
        corrected = replace_csv_value(exported, supplier.id, "display_name", "安科（已核对）")

        preview = preview_supplier_import(repo, corrected)
        _, created = create_supplier_change_requests(repo, corrected)

        self.assertTrue(preview["valid"])
        self.assertEqual(preview["changes"][0]["supplier_id"], supplier.id)
        self.assertTrue(preview["changes"][0]["requires_approval"])
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].approval_status, "pending")
        self.assertNotEqual(supplier.display_name, "安科（已核对）")

    def test_preview_reports_missing_stable_id_and_mismatched_erp_id(self) -> None:
        repo = seed_demo(InMemoryRepository())
        supplier = next(iter(repo.suppliers.values()))
        content = export_suppliers_csv(repo)
        no_id = replace_csv_value(content, supplier.id, "id", "")
        wrong_erp = replace_csv_value(content, supplier.id, "erp_id", "SUP-WRONG")

        self.assertEqual(
            preview_supplier_import(repo, no_id)["errors"][0]["code"],
            "stable_id_required",
        )
        self.assertEqual(
            preview_supplier_import(repo, wrong_erp)["errors"][0]["code"],
            "erp_id_mismatch",
        )

    def test_duplicate_candidates_never_auto_merge(self) -> None:
        repo = seed_demo(InMemoryRepository())
        original = next(iter(repo.suppliers.values()))
        duplicate = Supplier(
            erp_id="SUP-DUP",
            legal_name="安科制造（苏州）有限公司",
            display_name=original.display_name,
            aliases=[original.display_name],
        )
        repo.suppliers[duplicate.id] = duplicate

        candidates = duplicate_supplier_candidates(repo)

        match = next(
            item
            for item in candidates
            if {item["left"]["id"], item["right"]["id"]} == {original.id, duplicate.id}
        )
        self.assertFalse(match["automatic_merge"])


if __name__ == "__main__":
    unittest.main()
