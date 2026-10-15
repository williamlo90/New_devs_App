# Revenue tenant isolation tests

From the repository root, install the backend requirements plus `pytest` and
`pytest-asyncio` in a virtual environment. These test dependencies are also in
the backend's `uv` development group.

```powershell
python -m pip install -r backend/requirements.txt pytest pytest-asyncio
docker compose up -d db redis

# One-time setup of a dedicated test database (never use production data).
docker compose exec -T db createdb -U postgres propertyflow_isolation_test
Get-Content database/schema.sql | docker compose exec -T db psql -U postgres -d propertyflow_isolation_test -v ON_ERROR_STOP=1
Get-Content database/seed.sql | docker compose exec -T db psql -U postgres -d propertyflow_isolation_test -v ON_ERROR_STOP=1

$env:TEST_DATABASE_URL='postgresql://postgres:postgres@localhost:5433/propertyflow_isolation_test'
$env:TEST_REDIS_URL='redis://localhost:6380/15'
Set-Location backend
python -m pytest tests -q
```

Integration tests authenticate through the real login route and authentication
dependency, then execute the dashboard routes against PostgreSQL and Redis.
Redis database 15 is reserved for these tests and cleared between cases. The
access-revocation test temporarily removes and restores Ocean's empty property
in the dedicated test database. Do not run this suite concurrently against the
same test database.

Without `TEST_DATABASE_URL`, only the cache unit tests run; integration cases
are explicitly skipped. The cache tests substitute Redis and the calculator,
while integration cases exercise the real queries, authorization, and cache.

Frontend refresh-storage regression tests use Node's built-in test runner and
the existing TypeScript dependency. From `frontend`:

```powershell
node --test tests/session-storage.test.cjs
```

Browser checks: log in as Sunset, verify only its three properties and
`prop-001` revenue of USD 2,250.00; refresh twice. Sign out through the account
menu, log in as Ocean, verify only its three properties and `prop-001` revenue
of USD 0.00; refresh twice. Ocean's Lakeside Cottage has USD 1,776.50.


## Revenue accuracy contract

`GET /api/v1/dashboard/summary` accepts `month` and `year` together; omitting
both retains all-time reporting. Monthly revenue recognizes the full reservation
amount at check-in, using the property's timezone and [local month start, next
local month start). PostgreSQL converts both boundaries independently to instants.
The dashboard's month selector exposes this filter without changing the default.

`total_revenue` is now a decimal string; `display_total_revenue` is its exact
two-decimal presentation. Consumers must not convert these to binary floats.
The existing Math.round tie direction (toward positive infinity) is retained,
with rounding only after aggregation. This preserves the existing display rule;
it does not define a new per-currency accounting policy. A single currency is
reported as stored; mixed or missing currencies return 422. Empty reports retain
the previous USD default. Cache v3 separates tenant, property, period and timezone
and ignores prior cached calculations, with the existing 300-second TTL.

`test_revenue_accuracy.py` exercises real database month boundaries, both March
DST transitions, leap February, December rollover, monthly tenant isolation,
cache hits/misses, fractional aggregation, positive/negative rounding ties,
and rejection of mixed/unknown currencies. Run with the same environment above.
