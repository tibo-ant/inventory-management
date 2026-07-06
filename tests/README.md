# API Tests

Comprehensive test suite for the Factory Inventory Management System backend APIs.

## Test Structure

```
tests/
├── pytest.ini          # Pytest configuration
├── backend/            # Backend API tests
│   ├── conftest.py     # Test fixtures and configuration
│   ├── test_dashboard.py      # Dashboard endpoint tests (13 tests)
│   ├── test_inventory.py      # Inventory endpoint tests (10 tests)
│   ├── test_misc_endpoints.py # Demand, backlog, spending, root tests (18 tests)
│   └── test_restock.py        # Restocking endpoint tests (28 tests)
└── README.md           # This file
```

## Running Tests

### Run all tests
```bash
cd tests
uv run pytest -v
```

### Run specific test file
```bash
cd tests
uv run pytest backend/test_inventory.py -v
```

### Run specific test class
```bash
cd tests
uv run pytest backend/test_inventory.py::TestInventoryEndpoints -v
```

### Run specific test
```bash
cd tests
uv run pytest backend/test_inventory.py::TestInventoryEndpoints::test_get_all_inventory -v
```

### Run with coverage (requires pytest-cov)
```bash
cd tests
uv run pytest --cov=../server --cov-report=html
```

## Test Coverage

**Total: 69 tests** covering all API endpoints. See [TEST_SUMMARY.md](TEST_SUMMARY.md)
for the full per-suite breakdown.

### Dashboard Endpoints (13 tests)
- Summary retrieval, data types, non-negative values
- Filtering by warehouse, category, status, month, and combinations
- Calculation accuracy (pending orders, low stock, total inventory value)

### Inventory Endpoints (10 tests)
- Retrieval, filtering (warehouse, category, combinations, "all")
- Get by ID and 404 handling
- Field structure (including `lead_time_days`), types, non-negative values

### Demand, Backlog, Spending, Root (18 tests)
- Demand forecasts: trends, values, stable items change < 2%, and every
  forecast SKU resolves to a real inventory item
- Backlog: priorities, quantity logic, days delayed, and every backlog SKU
  resolves to a real inventory item
- Spending: summary, monthly (all cost categories, varied values),
  categories, transactions
- Root: API info and response structure

### Restocking Endpoints (28 tests)
- `GET /api/restock/recommendations`: budget bounds, shortfall-only
  recommendations, greedy allocation never exceeding the budget, partial
  fills, priority ordering, agreement with inventory, validation errors,
  and a float-precision regression (exact budgets buy exact quantities)
- `POST /api/restock/orders`: happy path, server-side re-pricing (client
  prices ignored), lead time = max of items, expected delivery date,
  sequential order numbers, and every 400/422 rejection path
- `GET /api/restock/orders`: empty at start, newest first, never mixed
  into the customer orders endpoint

## Test Features

- **FastAPI TestClient**: Uses FastAPI's built-in test client for fast, isolated testing
- **Fixtures**: Reusable test fixtures in `conftest.py`
- **Comprehensive Validation**: Tests data structure, types, calculations, and business logic
- **Filter Testing**: Validates all filter combinations and edge cases
- **Error Handling**: Tests 404 responses and edge cases
- **New Features**: Includes tests for Power Supplies category

## Dependencies

Tests require the following packages (automatically installed with `uv sync`):
- pytest >= 8.0.0
- pytest-asyncio >= 0.23.0
- httpx >= 0.27.0
- pytest-cov >= 4.1.0 (optional, for coverage reports)

## Adding New Tests

1. Create test file in `tests/backend/` following naming convention `test_*.py`
2. Import `client` fixture from conftest.py
3. Create test class (optional but recommended for organization)
4. Write test functions starting with `test_`
5. Run tests to verify

Example:
```python
class TestNewEndpoint:
    def test_new_feature(self, client):
        response = client.get("/api/new-endpoint")
        assert response.status_code == 200
        data = response.json()
        assert "expected_field" in data
```

## CI/CD Integration

To integrate with CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Run API Tests
  run: |
    cd tests
    uv run pytest -v --tb=short
```

## Notes

- All tests use in-memory mock data (no database required)
- Tests are independent and can run in any order
- FastAPI TestClient handles app lifecycle automatically
- Tests run in ~0.13 seconds
