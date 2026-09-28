# Client home search

The client app reads Repliers sandbox listings through the CRM. Set `REPLIERS_API_KEY` on the web service. Never put this key in the iOS bundle or a public configuration file.

Public routes require an active brokerage with its client app enabled:

- `GET /api/client/v1/discovery/brokerages/<slug>/listings` searches active Texas sale listings. Supported filters are `city`, `max_price`, `min_beds`, `min_baths`, `property_type`, and `page`.
- The same route accepts `ids`, a comma-separated list of up to 40 listing IDs, to restore saved homes.
- `GET /api/client/v1/discovery/brokerages/<slug>/listings/<id>` loads one property.

IDs include the provider board and MLS number, such as `rp-110-TEST123`. Existing `h01` through `h15` saves remain valid for older app versions. The new app no longer fills its search with those fixtures.

Responses include public photos, coordinates when map display is permitted, and property facts supplied by Repliers. Search uses 24 results per page and a two-minute bounded process cache. Provider failures return a retryable error without exposing credentials or replacing results with invented properties.

This integration deliberately serves sandbox records only. Connecting a licensed MLS feed requires a separate review of attribution, display permissions, geographic scope, and the sandbox labels in both apps.

## Deployment and testing

Apply Alembic revision `add_inquiry_listing_snapshot` before deploying. It adds a nullable JSON column to existing inquiries so the CRM Messages inbox retains the address even if the listing later disappears. It does not change RLS policies or existing inquiry ownership.

Configure `REPLIERS_API_KEY` on the Railway web service, deploy the CRM, then install the updated iOS app. Open Homes, change city and price filters, tap the card edges to browse five photos, and open details for the complete gallery and supplied facts. Save a home, sign out and back in, then send a test inquiry. The agent should find its address under Messages in the CRM.

Backend checks: `pytest tests/test_repliers_listings.py tests/test_client_discovery.py tests/test_client_messages.py`. The iOS repository also includes an isolated Flask fixture and a native URLSession contract check. Neither test sends email or needs a Repliers key.

Repliers MCP uses its own OAuth connection and Developer Portal key link. The CRM REST integration does not depend on that MCP connection or an OpenAI key.

## Brokerage identity

Origen's default app identity uses the marketing email `CLIENT_EMAIL_BRAND_MARK` and `CLIENT_EMAIL_BRAND_WORDMARK` assets. Its branding response includes `brand_style: origen` and the wordmark URL. The iOS app bundles transparent versions of these PNGs for its initial and offline presentation. An uploaded organization logo takes precedence and clears the built-in style and wordmark. Other brokerages never receive the Origen defaults. No database migration is needed for these response fields.
