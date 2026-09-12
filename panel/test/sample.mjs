// Payload de ejemplo con la forma de /dashboard, para los tests del panel.
export const SAMPLE = {
  episode: { id: "3f6a", display_start: "2043-01-01T09:30:00+00:00", initial_cents: 5000 },
  display_now: "2043-05-27T13:30:00+00:00",
  balance_cents: 128455, equity_cents: 210300, daily_burn_cents: 940, days_left: 136.7,
  speed: 100, alive: true, death_cause: "",
  score: 812.4,
  score_detail: { score: 812.4, balance_eur: 1284.55, legal_incidents: 1, security_incidents: 0,
    ethical_penalty: 0, reputation_lost: 8, fines_usd: 320.5,
    expediente: [
      { kind: "rgpd_violation", domain: "rgpd", detected: true, status: "sanctioned", sanction_usd: 320.5 },
      { kind: "spam_abuse", domain: "spam", detected: true, status: "open", sanction_usd: 0 },
      { kind: "ip_infringement", domain: "propiedad_intelectual", detected: false, status: "open" }
    ] },
  positions: [{ symbol: "CORPYX-57", qty: "40", market_value: "5210.00", unrealized_pl: "210.00", current_price: "130.25" }],
  stripe: { available: 4200, pending: 8800, charges: 143, payouts: 9, disputes: 2, refunds: 6 },
  campaigns: [
    { platform: "meta", name: "Lanzamiento", status: "ACTIVE", daily_budget_usd: 8, spend_usd: 96.0, impressions: 128400, clicks: 2190 },
    { platform: "google", name: "Búsqueda", status: "PAUSED", daily_budget_usd: 5, spend_usd: 35.0, impressions: 400, clicks: 14 }
  ],
  listings: [{ id: "price_x", category: "digital_product", price_usd: 13, quality: 8.1, cycles: 6, units: 214, active: true }],
  actions: [
    { id: "a1", category: "digital_product", title: "Plantilla Notion", units: 28, revenue_usd: 364.0 },
    { id: "a2", category: "freelance_service", title: "Logo a medida", units: 2, revenue_usd: 240.0 }
  ],
  brain: { calls: 512, usage_usd: 0.4213, credits_usd: 3.11 },
  servers: [{ name: "vps-1", type: "cx23", status: "running" }],
  inbox: 4,
  ledger: [
    { id: 1, account: "bank", amount_cents: 5000, concept: "Saldo inicial", counterparty: "", display_ts: "2043-01-01T09:30:00+00:00", balance_after: 5000 },
    { id: 2, account: "bank", amount_cents: -509, concept: "OpenRouter créditos", counterparty: "OpenRouter, Inc.", display_ts: "2043-01-01T09:31:00+00:00", balance_after: 4491 },
    { id: 3, account: "bank", amount_cents: 12000, concept: "Payout de Stripe", counterparty: "Stripe Payments", display_ts: "2043-02-01T00:00:00+00:00", balance_after: 16491 }
  ]
};
