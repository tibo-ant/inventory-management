import threading
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders, restock_orders

app = FastAPI(title="Factory Inventory Management System")

# Upper bound on a restocking budget. Purely a sanity ceiling so an absurd
# query value can't drive the greedy allocator (or the UI) into nonsense.
MAX_RESTOCK_BUDGET = 10_000_000

# Restock urgency: items whose demand is growing are replenished first.
TREND_PRIORITY = {'increasing': 0, 'stable': 1, 'decreasing': 2}

# Serializes restock order-number allocation: the endpoint is a sync handler
# (FastAPI runs it in a threadpool), so two concurrent submissions could
# otherwise both read len(restock_orders) before either appends and mint the
# same id / order number.
_restock_order_lock = threading.Lock()

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

def inventory_by_sku() -> dict:
    """Index the inventory items by SKU.

    Rebuilt per call rather than cached at import so callers always see the
    current in-memory list (tests, and any future write endpoint, mutate it).
    """
    return {item['sku']: item for item in inventory_items}

def build_restock_recommendations(budget: float) -> dict:
    """Recommend which items to restock, and how many, within `budget`.

    Each demand forecast is joined to its inventory record by SKU. An item's
    shortfall is how far its forecasted demand exceeds what is on hand; items
    with no shortfall are never recommended. Candidates are ranked by urgency
    (growing demand first, then the largest shortfall) and the budget is
    spent greedily down that list. When an item's full shortfall no longer
    fits, we still buy as many whole units as the remaining budget covers
    before moving on, so cheaper items further down can be partially filled
    instead of the budget going unused.
    """
    items_by_sku = inventory_by_sku()

    candidates = []
    for forecast in demand_forecasts:
        item = items_by_sku.get(forecast['item_sku'])
        # Forecasts must reference a real inventory item to be priceable;
        # silently skip any that don't rather than failing the whole request.
        if not item:
            continue
        shortfall = max(0, forecast['forecasted_demand'] - item['quantity_on_hand'])
        if shortfall == 0:
            continue
        candidates.append((forecast, item, shortfall))

    # SKU is the final tiebreak so the ordering is deterministic.
    candidates.sort(key=lambda c: (TREND_PRIORITY.get(c[0]['trend'], 3), -c[2], c[1]['sku']))

    # Allocate in integer cents. Float floor-division of dollar amounts loses
    # a unit whenever the remaining budget is an exact multiple of the unit
    # cost (e.g. 11193.0 // 15.99 == 699.0, not 700), so a budget that exactly
    # covers a shortfall would come up one item short.
    remaining_cents = int(round(budget * 100))
    recommendations = []
    for forecast, item, shortfall in candidates:
        unit_cents = int(round(item['unit_cost'] * 100))
        # An unpriced item can never be budget-allocated (and guards the division).
        if unit_cents <= 0:
            continue
        # Whole units only: the full shortfall if it fits, otherwise however
        # many units the remaining budget still covers.
        quantity = min(shortfall, remaining_cents // unit_cents)
        if quantity < 1:
            continue
        line_cents = quantity * unit_cents
        remaining_cents -= line_cents
        recommendations.append({
            'sku': item['sku'],
            'name': item['name'],
            'category': item['category'],
            'warehouse': item['warehouse'],
            'unit_cost': item['unit_cost'],
            'quantity_on_hand': item['quantity_on_hand'],
            'forecasted_demand': forecast['forecasted_demand'],
            'trend': forecast['trend'],
            'shortfall': shortfall,
            'recommended_quantity': quantity,
            # Integer cents back to dollars: exact for 2-decimal prices.
            'line_total': line_cents / 100,
            'lead_time_days': item['lead_time_days'],
        })

    total_cost = round(sum(r['line_total'] for r in recommendations), 2)
    return {
        'budget': budget,
        'total_cost': total_cost,
        'remaining_budget': round(budget - total_cost, 2),
        'recommendations': recommendations,
    }

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str
    lead_time_days: int

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockRecommendation(BaseModel):
    sku: str
    name: str
    category: str
    warehouse: str
    unit_cost: float
    quantity_on_hand: int
    forecasted_demand: int
    trend: str
    shortfall: int
    recommended_quantity: int
    line_total: float
    lead_time_days: int

class RestockRecommendationsResponse(BaseModel):
    budget: float
    total_cost: float
    remaining_budget: float
    recommendations: List[RestockRecommendation]

class RestockOrderItemRequest(BaseModel):
    """A single line in a submitted restocking order.

    Deliberately only sku + quantity: prices, names and lead times are
    always re-derived server-side from inventory, so a client can never
    submit its own costs.
    """
    sku: str
    quantity: int = Field(ge=1, le=1_000_000)

class CreateRestockOrderRequest(BaseModel):
    budget: float = Field(gt=0, le=MAX_RESTOCK_BUDGET)
    items: List[RestockOrderItemRequest] = Field(min_length=1, max_length=100)

class RestockOrderItem(BaseModel):
    sku: str
    name: str
    category: str
    warehouse: str
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int

class RestockOrder(BaseModel):
    id: str
    order_number: str
    status: str
    created_date: str
    expected_delivery: str
    lead_time_days: int
    budget: float
    total_cost: float
    items: List[RestockOrderItem]

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/restock/recommendations", response_model=RestockRecommendationsResponse)
def get_restock_recommendations(budget: float = Query(default=0, ge=0, le=MAX_RESTOCK_BUDGET)):
    """Recommend items to restock within the given budget.

    Recommendations are derived from the demand forecasts joined to
    inventory (see build_restock_recommendations). A budget of 0 returns an
    empty recommendation list, which the UI uses as its initial state.
    """
    return build_restock_recommendations(budget)

@app.post("/api/restock/orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Submit a restocking order.

    Everything money-related is recomputed server-side from inventory: the
    request only carries SKUs and quantities, and the resulting total is
    checked against the submitted budget before the order is accepted.
    """
    items_by_sku = inventory_by_sku()

    # Reject duplicate SKUs so a quantity can't be split across lines to
    # obscure how much of one item is being ordered.
    skus = [entry.sku for entry in request.items]
    if len(skus) != len(set(skus)):
        raise HTTPException(status_code=400, detail="Duplicate SKUs in restocking order")

    order_items = []
    for entry in request.items:
        item = items_by_sku.get(entry.sku)
        if not item:
            raise HTTPException(status_code=400, detail=f"Unknown inventory SKU: {entry.sku}")
        order_items.append({
            'sku': item['sku'],
            'name': item['name'],
            'category': item['category'],
            'warehouse': item['warehouse'],
            'quantity': entry.quantity,
            'unit_cost': item['unit_cost'],
            'line_total': round(entry.quantity * item['unit_cost'], 2),
            'lead_time_days': item['lead_time_days'],
        })

    total_cost = round(sum(line['line_total'] for line in order_items), 2)
    if total_cost > request.budget:
        raise HTTPException(
            status_code=400,
            detail=f"Order total ${total_cost:,.2f} exceeds the available budget ${request.budget:,.2f}"
        )

    # The whole order arrives together, so delivery is gated by the slowest
    # supplier among its items.
    lead_time_days = max(line['lead_time_days'] for line in order_items)
    created = datetime.now()

    # The number must be read and reserved atomically (see _restock_order_lock).
    with _restock_order_lock:
        order_id = len(restock_orders) + 1
        order = {
            'id': str(order_id),
            'order_number': f"RST-{created.year}-{order_id:04d}",
            'status': 'Submitted',
            'created_date': created.strftime('%Y-%m-%dT%H:%M:%S'),
            'expected_delivery': (created + timedelta(days=lead_time_days)).strftime('%Y-%m-%dT%H:%M:%S'),
            'lead_time_days': lead_time_days,
            'budget': request.budget,
            'total_cost': total_cost,
            'items': order_items,
        }
        restock_orders.append(order)
    return order

@app.get("/api/restock/orders", response_model=List[RestockOrder])
def get_restock_orders():
    """List restocking orders submitted this session, newest first."""
    return list(reversed(restock_orders))

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
