# Integration adapters

## ERPNext

Set `ERP_NEXT_BASE_URL`, `ERP_NEXT_API_KEY`, and `ERP_NEXT_API_SECRET`. The adapter uses Frappe token authentication and `/api/resource` for Supplier, Contact, Address, and Visit Requirement documents. All DocType and field names are centralized in `ERPNextFieldMap`; override that mapping when custom fields differ.

## Microsoft Graph

Set `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID`, and `MICROSOFT_CLIENT_SECRET`. Grant application permissions for calendar read/write and configure an explicit calendar user for a production deployment. The adapter caches OAuth tokens, creates tentative holds, confirms/updates/cancels events, queries schedules, and consumes paginated delta sync results. Missing credentials leave the mock adapter active.

## Feishu

Set `CALENDAR_PROVIDER=feishu`, `FEISHU_APP_ID`, and `FEISHU_APP_SECRET`. The adapter uses tenant access token authentication and Feishu Calendar v4 endpoints for busy lookup, tentative event creation, confirmation/update, and cancellation. `FEISHU_CALENDAR_ID` defaults to `primary`; set the concrete calendar ID for production.

## Excel ERP substitute

Set `ERP_PROVIDER=excel` and `ERP_EXCEL_PATH` to a CSV or simple `.xlsx` exported from ERPNext-style supplier data. The Alpha loader accepts columns such as `erp_id`, `display_name`, `legal_name`, `site_name`, `address`, `latitude`, `longitude`, `contact_name`, `email`, and `phone`. This is useful for pilots before ERPNext write-back is available.

## Serper Search

Set `SEARCH_PROVIDER=serper`, `SERPER_API_KEY`, and `SERPER_URL`. The `/api/v1/search` endpoint requires the `coordinator` role and normalizes organic results, answer box, and knowledge graph payloads.

## Nominatim

Set `GEOCODING_PROVIDER=nominatim`, `NOMINATIM_BASE_URL`, and a descriptive `NOMINATIM_USER_AGENT`. Requests are rate limited and cached. Public Nominatim instances are unsuitable for bulk production imports; deploy a private instance or provider-compatible endpoint.

## OSRM

Set `ROUTING_PROVIDER=osrm` and `OSRM_BASE_URL`. Planning prefetches the Table API matrix and uses those durations inside CP-SAT. Itinerary geometry is available through the Route API. Cache keys include provider, profile, rounded coordinates, and the date bucket. Haversine remains the explicit estimated fallback.
