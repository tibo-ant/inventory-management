# Test Summary

## Test Coverage Overview

All backend API tests are passing with **69 tests** across four files in `tests/backend/`.

## Test Suites

### 1. Dashboard Endpoints — `test_dashboard.py` (13 tests)
- Dashboard summary retrieval
- Data type validation
- Non-negative value validation
- Filtering by warehouse, category, status, and month
- Multiple filter combinations
- Power Supplies category support
- **Actual calculations** for:
  - Pending orders count (Processing + Backordered)
  - Low stock items (at or below reorder point)
  - Total inventory value (quantity x unit cost)

### 2. Inventory Endpoints — `test_inventory.py` (10 tests)
- Get all inventory items
- Filter by warehouse
- Filter by category (including Power Supplies)
- Combined warehouse and category filtering
- "all" filter handling
- Get specific item by ID
- 404 handling for non-existent items
- Required fields validation (including `lead_time_days`)
- Quantity and cost type validation
- Non-negative value validation

### 3. Demand, Backlog, Spending, Root — `test_misc_endpoints.py` (18 tests)
- **Demand forecasts (5)**: retrieval, valid trend values, non-negative values,
  stable items change by less than 2%, and every forecast SKU + name resolves
  to a real inventory item (referential integrity)
- **Backlog (5)**: retrieval, valid priorities, quantity logic, days delayed,
  and every backlog SKU + name resolves to a real inventory item
- **Spending (6)**: summary, monthly (all cost categories present, values vary),
  categories, transactions
- **Root (2)**: API info and response structure

### 4. Restocking Endpoints — `test_restock.py` (28 tests)
- **`GET /api/restock/recommendations` (14)**
  - Default budget of 0 recommends nothing
  - Response structure and types
  - Only items with a shortfall (forecast above stock) are recommended
  - Total never exceeds the budget; remaining budget is consistent
  - Line totals equal quantity x unit cost
  - Recommended quantity never exceeds the shortfall
  - An unconstrained budget fully covers every shortfall
  - A tighter budget buys strictly less
  - Partial fill of whole units when a full shortfall no longer fits
    (budget derived from the data, not hardcoded)
  - **Regression**: a budget exactly covering a shortfall buys the full
    shortfall (integer-cent allocation, not float floor-division)
  - Priority ordering (increasing trend first, then largest shortfall)
  - Recommendations agree with the inventory records they reference
  - Negative and over-limit budgets rejected with 422
- **`POST /api/restock/orders` (11)**
  - Successful submission returns 201 with a fully-formed order
  - Submitted orders appear in the listing
  - Client-supplied prices are ignored; the server re-prices from inventory
  - Order total equals the sum of inventory-priced lines
  - Order lead time is the maximum of its items' lead times
  - Expected delivery equals creation date + lead time
  - Sequential order numbers
  - Unknown SKU, over-budget total, and duplicate SKUs return 400
    (and persist nothing)
  - Malformed payloads (zero/negative quantity, empty items, zero or
    over-limit budget, missing fields) return 422
- **`GET /api/restock/orders` (3)**
  - Empty at start of a session
  - Newest-first ordering
  - Restocking orders never leak into the customer orders endpoint

## Key Testing Principles

### No Hardcoded Success Values
Tests verify actual calculations and real data relationships. Budgets used to
probe edge cases in the restocking allocator are derived from the live data,
so a valid edit to the sample JSON does not silently invalidate them.

### Isolation
Restocking orders accumulate in an in-memory list; an autouse fixture clears
it around every test so tests are independent of execution order. The float
regression test registers (and removes) its own synthetic inventory item and
forecast rather than depending on the sample data.

### Referential Integrity
Both demand forecasts and backlog items are asserted to reference real
inventory SKUs with matching names, because the restocking recommender and
the UI join those datasets to inventory.

## Running the Tests

```bash
cd tests
uv run pytest backend/ -v
```

## Test Results
- **Total Tests**: 69
- **Passed**: 69
- **Failed**: 0
