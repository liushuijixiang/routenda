# OpenRefine supplier reconciliation

1. Export `/api/v1/reconciliation/suppliers/export` and retain the `id` column.
2. Import the CSV into OpenRefine and reconcile names or statuses without changing `id` or `erp_id`.
3. Export as CSV with the original headers.
4. Send the CSV text to `/api/v1/reconciliation/suppliers/import-preview` and resolve every row error.
5. Send the same text to `/api/v1/reconciliation/suppliers/import-apply`.

Apply creates approval-bound `MasterDataChangeRequest` records. It does not directly update ERP-owned supplier fields, and duplicate candidates are never merged automatically.
