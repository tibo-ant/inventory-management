<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <label class="budget-label" for="budget-range">{{ t('restocking.budgetLabel') }}</label>
      <div class="budget-readout">{{ currencySymbol }}{{ Math.round(budget).toLocaleString() }}</div>
      <input
        id="budget-range"
        v-model.number="budget"
        type="range"
        min="0"
        :max="BUDGET_MAX"
        step="500"
        class="budget-range"
      />
      <div class="budget-ticks">
        <span>{{ currencySymbol }}0</span>
        <span>{{ currencySymbol }}{{ BUDGET_MAX.toLocaleString() }}</span>
      </div>
      <p class="budget-hint">{{ t('restocking.budgetHint') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.budget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recs.budget.toLocaleString() }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.summary.orderTotal') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recs.total_cost.toLocaleString() }}</div>
        </div>
        <div :class="['stat-card', { success: recs.remaining_budget > 0 }]">
          <div class="stat-label">{{ t('restocking.summary.remaining') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recs.remaining_budget.toLocaleString() }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.itemCount') }}</div>
          <div class="stat-value">{{ recs.recommendations.length }}</div>
        </div>
      </div>

      <!-- Success panel replaces the recommendations card + button once an order has been placed -->
      <div v-if="submittedOrder" class="card success-panel">
        <p class="success-title">{{ t('restocking.orderPlaced', { orderNumber: submittedOrder.order_number }) }}</p>
        <p class="success-detail">{{ t('restocking.orderPlacedDetail', { days: submittedOrder.lead_time_days }) }}</p>
        <div class="success-actions">
          <router-link to="/orders" class="btn-primary">{{ t('restocking.viewInOrders') }}</router-link>
          <button class="btn-secondary" @click="resetForNewOrder">{{ t('restocking.placeAnother') }}</button>
        </div>
      </div>

      <div v-else class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }}</h3>
        </div>

        <div v-if="recs.recommendations.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.warehouse') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.onHand') }}</th>
                <th>{{ t('restocking.table.forecast') }}</th>
                <th>{{ t('restocking.table.shortfall') }}</th>
                <th>{{ t('restocking.table.orderQty') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in recs.recommendations" :key="r.sku">
                <td><strong>{{ r.sku }}</strong></td>
                <td>{{ translateProductName(r.name) }}</td>
                <td>{{ translateWarehouse(r.warehouse) }}</td>
                <td>
                  <span :class="['badge', r.trend]">{{ t(`trends.${r.trend}`) }}</span>
                </td>
                <td>{{ r.quantity_on_hand }}</td>
                <td>{{ r.forecasted_demand }}</td>
                <td>{{ r.shortfall }}</td>
                <td>
                  <strong>{{ r.recommended_quantity }}</strong>
                  <span
                    v-if="r.recommended_quantity === r.shortfall"
                    class="badge success fill-badge"
                  >{{ t('restocking.fill.full') }}</span>
                  <span v-else class="badge warning fill-badge">{{ t('restocking.fill.partial') }}</span>
                </td>
                <td>{{ currencySymbol }}{{ r.unit_cost }}</td>
                <td><strong>{{ currencySymbol }}{{ r.line_total.toLocaleString() }}</strong></td>
                <td>{{ t('restocking.leadTimeDays', { days: r.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="place-order-row">
          <button
            class="btn-primary"
            :disabled="recs.recommendations.length === 0 || submitting || refreshing"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
          <div v-if="submitError" class="error">{{ submitError }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName, translateWarehouse } = useI18n()

    const currencySymbol = computed(() => {
      return currentCurrency.value === 'JPY' ? '¥' : '$'
    })

    // Single source of truth for the slider ceiling; the API accepts far more
    // but this is a sensible UI range for the sample data.
    const BUDGET_MAX = 100000

    const budget = ref(50000)
    const recs = ref({ budget: 0, total_cost: 0, remaining_budget: 0, recommendations: [] })
    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const submitError = ref(null)
    const submittedOrder = ref(null)
    // True while a slider-driven refetch is in flight and hasn't yet caught up
    // to the currently-displayed plan.
    const refreshing = ref(false)
    // Debounce timer for the budget slider (see watch() below for why); scoped
    // to setup() so each component instance gets its own timer.
    let debounceTimer = null
    // Monotonically increasing request sequence number so an out-of-order
    // response from a stale (superseded) budget can be detected and ignored.
    let requestSeq = 0

    // Restocking recommendations are computed across all warehouses, so this view
    // intentionally does not use the global useFilters composable (same as Reports.vue).
    // `loading` gates the initial render only; slider-driven refetches update the table
    // in place so the page doesn't blank on every adjustment (no `loading.value = true` here).
    const loadRecommendations = async () => {
      // Capture the sequence number for THIS request; if a newer request has
      // started by the time this one resolves, its result is stale and must
      // not overwrite the plan for the budget currently shown.
      const seq = ++requestSeq
      try {
        error.value = null
        const result = await api.getRestockRecommendations(budget.value)
        if (seq !== requestSeq) return
        recs.value = result
      } catch (err) {
        if (seq !== requestSeq) return
        error.value = t('restocking.loadError')
      } finally {
        if (seq === requestSeq) refreshing.value = false
        loading.value = false
      }
    }

    // The range input fires a change event continuously while the user drags it,
    // so we debounce here to avoid firing an HTTP request per pixel of movement.
    watch(budget, () => {
      // The visible plan no longer matches the slider the instant it moves, so
      // Place Order must be greyed out until the matching plan arrives (not just
      // once the debounced fetch kicks off).
      refreshing.value = true
      // Changing the budget starts a new restocking decision, so dismiss any
      // previously-placed order confirmation instead of leaving it stale on screen.
      submittedOrder.value = null
      submitError.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        loadRecommendations()
      }, 250)
    })

    const placeOrder = async () => {
      submitting.value = true
      submitError.value = null
      try {
        // Only sku + quantity are sent — the server re-derives all prices/costs,
        // so the client never sends cost data it doesn't own.
        // Use the budget the DISPLAYED plan was computed for, not the live slider
        // value, so the payload is always self-consistent.
        const payload = {
          budget: recs.value.budget,
          items: recs.value.recommendations.map(r => ({ sku: r.sku, quantity: r.recommended_quantity }))
        }
        submittedOrder.value = await api.createRestockOrder(payload)
      } catch (err) {
        submitError.value = err.response?.data?.detail || t('restocking.orderError')
      } finally {
        submitting.value = false
      }
    }

    const resetForNewOrder = () => {
      submittedOrder.value = null
      submitError.value = null
      loadRecommendations()
    }

    onMounted(loadRecommendations)

    // Prevents a pending debounced fetch from firing after the user navigates away.
    onUnmounted(() => clearTimeout(debounceTimer))

    return {
      t,
      currencySymbol,
      translateProductName,
      translateWarehouse,
      budget,
      recs,
      loading,
      error,
      submitting,
      submitError,
      submittedOrder,
      refreshing,
      BUDGET_MAX,
      placeOrder,
      resetForNewOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  display: flex;
  flex-direction: column;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-readout {
  font-size: 2.25rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  margin: 0.375rem 0 1rem;
}

.budget-range {
  width: 100%;
  accent-color: #3b82f6;
}

.budget-ticks {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #94a3b8;
  margin-top: 0.375rem;
}

.budget-hint {
  color: #64748b;
  font-size: 0.875rem;
  margin-top: 0.75rem;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.fill-badge {
  margin-left: 0.5rem;
}

.place-order-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.75rem;
  margin-top: 1.25rem;
}

.btn-primary {
  display: inline-block;
  background: #0f172a;
  color: white;
  border: none;
  padding: 0.625rem 1.5rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.2s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1e293b;
}

.btn-primary:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}

.btn-secondary {
  display: inline-block;
  background: white;
  color: #0f172a;
  border: 1px solid #e2e8f0;
  padding: 0.625rem 1.5rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-secondary:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.success-panel {
  border-left: 4px solid #10b981;
}

.success-title {
  font-size: 1.063rem;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 0.375rem;
}

.success-detail {
  color: #64748b;
  font-size: 0.938rem;
  margin-bottom: 1.25rem;
}

.success-actions {
  display: flex;
  gap: 0.75rem;
}
</style>
