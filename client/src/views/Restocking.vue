<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budgetLabel') }}</h3>
        </div>
        <div class="budget-slider-row">
          <input
            type="range"
            class="budget-slider"
            min="0"
            :max="maxBudget"
            step="100"
            v-model.number="budget"
          />
          <span class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedTitle') }}</h3>
        </div>

        <div v-if="recommendations.length === 0" class="no-recommendations">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.shortfall') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.item_sku">
                <td>
                  <input type="checkbox" v-model="selected[rec.item_sku]" />
                </td>
                <td>{{ rec.item_name }}</td>
                <td><strong>{{ rec.item_sku }}</strong></td>
                <td>
                  <span :class="['badge', rec.trend]">
                    {{ t(`trends.${rec.trend}`) }}
                  </span>
                </td>
                <td>{{ rec.shortfall }}</td>
                <td>{{ formatCurrencyWithDecimals(rec.unit_cost, currentCurrency, 2) }}</td>
                <td><strong>{{ rec.qty }}</strong></td>
                <td>{{ formatCurrency(rec.line_total, currentCurrency) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="recommendations.length > 0" class="order-footer">
          <div class="selected-total">
            {{ t('restocking.selectedTotal') }}: <strong>{{ formatCurrency(selectedTotal, currentCurrency) }}</strong>
          </div>

          <div v-if="submitError" class="error">{{ submitError }}</div>
          <div v-if="submitSuccess" class="success-message">{{ t('restocking.orderSuccess') }}</div>
          <div v-if="selectionWarning" class="error">{{ t('restocking.selectAtLeastOne') }}</div>

          <button
            class="place-order-btn"
            :disabled="submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, computed, watch } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency.js'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])

    const budget = ref(0)
    // selected is a map of item_sku -> boolean (checkbox state), defaulted to
    // checked whenever a recommendation row is (re)computed
    const selected = ref({})

    const submitting = ref(false)
    const submitError = ref(null)
    const submitSuccess = ref(false)
    const selectionWarning = ref(false)

    const loadForecasts = async () => {
      try {
        loading.value = true
        error.value = null
        forecasts.value = await api.getDemandForecasts()
        // Reset budget/selection state for a fresh recommendation run
        // (e.g. right after successfully placing an order)
        budget.value = Math.round(maxBudget.value * 0.4)
      } catch (err) {
        error.value = 'Failed to load demand forecasts: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // The theoretical ceiling for the budget slider: the cost to fully cover
    // every item's shortfall, rounded up to the nearest 100 so the slider has
    // a clean max value.
    const maxBudget = computed(() => {
      const total = forecasts.value.reduce((sum, item) => {
        const shortfall = Math.max(item.forecasted_demand - item.current_demand, 0)
        return sum + shortfall * item.unit_cost
      }, 0)
      if (total <= 0) return 0
      return Math.ceil(total / 100) * 100
    })

    // --- Recommendation / allocation algorithm ---
    // Given the current budget, decide which items to restock and how much:
    //   1. Compute each item's shortfall (forecasted demand beyond what's on
    //      hand today). Items with no shortfall need nothing and are dropped.
    //   2. Rank items by urgency: items with increasing demand are prioritized
    //      over stable, then decreasing; ties are broken by larger shortfall
    //      first, so the biggest gaps for the most urgent trend get funded first.
    //   3. Walk the ranked list and greedily spend the budget: for each item,
    //      buy as much of the shortfall as the remaining budget allows
    //      (never more than the shortfall itself). Once the remaining budget
    //      can't afford even a single unit of the next item, that item (and
    //      any item further down the ranking with the same or higher cost)
    //      is left out of the recommendation entirely.
    // This is a simple greedy allocator, not an optimal knapsack solve, but it
    // keeps the behavior easy to reason about and responsive as the slider moves.
    const recommendations = computed(() => {
      const trendPriority = { increasing: 0, stable: 1, decreasing: 2 }

      const candidates = forecasts.value
        .map(item => ({
          ...item,
          shortfall: Math.max(item.forecasted_demand - item.current_demand, 0)
        }))
        .filter(item => item.shortfall > 0)

      candidates.sort((a, b) => {
        const priorityDiff = trendPriority[a.trend] - trendPriority[b.trend]
        if (priorityDiff !== 0) return priorityDiff
        return b.shortfall - a.shortfall
      })

      let remainingBudget = budget.value
      const results = []

      for (const item of candidates) {
        const qty = Math.min(item.shortfall, Math.floor(remainingBudget / item.unit_cost))
        if (qty > 0) {
          const line_total = qty * item.unit_cost
          remainingBudget -= line_total
          results.push({
            ...item,
            qty,
            line_total
          })
        }
      }

      return results
    })

    // Keep the checkbox map in sync with the current recommendation list:
    // newly appearing rows default to checked, rows that disappear are
    // removed so stale entries don't linger in the selected map.
    watch(recommendations, (newRecs) => {
      const next = {}
      for (const rec of newRecs) {
        next[rec.item_sku] = rec.item_sku in selected.value ? selected.value[rec.item_sku] : true
      }
      selected.value = next
    }, { immediate: true })

    const selectedTotal = computed(() => {
      return recommendations.value
        .filter(rec => selected.value[rec.item_sku])
        .reduce((sum, rec) => sum + rec.line_total, 0)
    })

    const placeOrder = async () => {
      const checkedItems = recommendations.value
        .filter(rec => selected.value[rec.item_sku])
        .map(rec => ({ item_sku: rec.item_sku, quantity: rec.qty }))

      if (checkedItems.length === 0) {
        selectionWarning.value = true
        return
      }
      selectionWarning.value = false

      try {
        submitting.value = true
        submitError.value = null
        submitSuccess.value = false

        await api.createRestockingOrder({
          budget: budget.value,
          items: checkedItems
        })

        submitSuccess.value = true
        // Refresh demand data so the recommendation list is clean for another order
        await loadForecasts()
      } catch (err) {
        submitError.value = t('restocking.orderError')
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadForecasts)

    return {
      t,
      currentCurrency,
      formatCurrency,
      formatCurrencyWithDecimals,
      loading,
      error,
      budget,
      maxBudget,
      recommendations,
      selected,
      selectedTotal,
      submitting,
      submitError,
      submitSuccess,
      selectionWarning,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-slider-row {
  display: flex;
  align-items: center;
  gap: 1.25rem;
}

.budget-slider {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  outline: none;
  -webkit-appearance: none;
  appearance: none;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  cursor: pointer;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  cursor: pointer;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.budget-value {
  font-size: 1.125rem;
  font-weight: 700;
  color: #0f172a;
  min-width: 100px;
  text-align: right;
}

.no-recommendations {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}

.order-footer {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.75rem;
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid #e2e8f0;
}

.selected-total {
  font-size: 1rem;
  color: #334155;
}

.success-message {
  background: #d1fae5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.938rem;
}

.place-order-btn {
  background: #2563eb;
  color: white;
  border: none;
  padding: 0.625rem 1.5rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
</style>
