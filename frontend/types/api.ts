// Shapes returned by the FastAPI backend (subset used by the UI).
export type Action = "BUY_NOW" | "WAIT" | "MONITOR" | "BUY_IF_TARGET_REACHED" | "COMPARE_VENDORS" | "INSUFFICIENT_DATA";

export interface PricePoint { ts: string; price: number; kind?: string }

export interface DataQuality {
  recorded_points: number; recorded_days?: number; first_record?: string; last_record?: string; span_days?: number;
  max_gap_days?: number | null; days_since_last_record?: number; carried_forward_share?: number | null;
  unknown_day_share?: number | null; known_days?: number; sufficient_for_stats: boolean; sufficient_for_forecast: boolean;
  sufficient_for_seasonality?: boolean;
}

export interface Change { from?: number; to?: number; abs?: number; pct?: number; unknown?: boolean; reason?: string; reference_was_recorded?: boolean }

export interface ProductSummary {
  id: string; name: string; brand: string | null; category: string | null; currency: string; image_url: string | null;
  current: { price: number; as_of: string; kind: string } | null;
  current_offer_price: { price: number; as_of: string } | null;
  changes?: Record<string, Change | null>;
  historical_low?: { price: number; first_seen: string } | null;
  source_stated_stats?: Record<string, { value: number; observed_at: string }>;
  data_quality: DataQuality; last_collection_at: string | null; tracking_status: string; last_error: string | null;
}

export interface HistorySummary {
  product_id: string; current: ProductSummary["current"]; current_offer_price: ProductSummary["current_offer_price"]; last_offer_price_seen?: { price: number; as_of: string } | null;
  recorded_points_stats?: { min: number; max: number; median: number; n: number };
  daily_stats_all_time?: Stats | null; daily_stats_last_365d?: Stats | null;
  current_percentile_last_365d?: number | null; percentile_basis_days?: number;
  historical_low?: { price: number; first_seen: string }; changes?: Record<string, Change | null>;
  source_stated_stats?: Record<string, { value: number; observed_at: string }>;
  data_quality: DataQuality; limitations: string[];
}
export interface Stats { min: number; max: number; median: number; p10: number; p90: number; days: number }

export interface Forecast {
  status: string; message?: string; horizon_days: number; forecast_method: string; predicted_price: number | null;
  lower_price_bound: number | null; upper_price_bound: number | null; probability_below_target: number | null;
  probability_detail?: { band?: string | null; reason?: string; effective_samples?: number; skill_demonstrated?: boolean } | null;
  limitations: string[];
  validation_summary?: { chosen?: string; reason?: string; interval_holdout_coverage?: number | null;
    methods?: Record<string, { n: number; mae?: number; mape_pct?: number }> };
}

export interface SaleEvent {
  id?: number; event_name: string; retailer: string; category?: string; start_date: string | null; end_date: string | null;
  verification_status: string; status_label?: string; source_url: string | null; notes?: string | null; days_until?: number | null;
}

export interface EventWindow {
  event_name: string; start: string; end: string; median_before: number; min_during: number; median_during: number;
  median_after: number | null; pct_change_median: number; abs_saving_at_min: number; price_dropped: boolean;
  recorded_points_in_windows: number; data_quality: string; verification_status?: string;
}
export interface EventSummary {
  event_name: string; comparable_events: number; drop_frequency: string; median_pct_change: number;
  median_saving_at_min: number; typical_discount_days: number; sufficient: boolean; note: string | null;
  vs_non_event_periods: { unusually_large: boolean; share_of_non_event_windows_with_bigger_drop_pct: number } | null;
}
export interface PatternTest { supported: boolean; reason?: string; p_value?: number; groups?: Record<string, { n: number; median_rel_pct?: number }>; note?: string }
export interface Seasonality {
  relevant_events: SaleEvent[];
  event_analysis: { events: EventWindow[]; by_event: EventSummary[]; skipped_events?: { event_name: string; start: string; reason: string }[]; limitations: string[] };
  calendar_patterns: { day_of_week: PatternTest | null; month: PatternTest | null; month_end: PatternTest | null; method?: string };
  upcoming_events: SaleEvent[];
}

export interface Recommendation {
  id: number | null; product_id: string; product_name: string | null; generated_at: string; action: Action;
  current_price: number | null; current_price_as_of: string | null; target_price: number | null;
  forecast_horizon_days: number | null; probability_below_target: number | null; probability_band: string | null;
  recommended_wait_days: number | null; estimated_savings: number | null; estimated_savings_range: number[] | null;
  savings_explanation: string | null; confidence: "LOW" | "MEDIUM" | "HIGH"; reasoning: string[]; risks: string[];
  suggested_action: string; evidence: Record<string, unknown>; data_sources: Record<string, unknown>[];
  data_freshness: Record<string, unknown>; limitations: string[]; vendor_data_accessed: boolean; recommendation_version: string;
}

export interface Freshness { origin: string; fetched_at: string; age_hours: number; label: string }
export interface EmiCalc {
  status: string; financed_amount: string | null; monthly_emi: string | null; tenure_months: number | null;
  total_instalments: string | null; total_interest: string | null; processing_fee: string | null;
  processing_fee_gst: string | null; total_cost: string | null; extra_cost_vs_upfront: string | null;
  implied_annual_rate_pct: string | null; emi_source: string; no_cost_verdict: string | null;
  assumptions: string[]; missing: string[]; warnings: string[];
}
export interface EmiRow {
  emi_offer_id: number; bank: string | null; card_type: string | null; tenure_months: number | null;
  advertised_monthly_emi: string | null; interest_rate_pct: string | null; rate_type: string | null; no_cost_claim: boolean;
  calculation: EmiCalc; verification_status: string; label: string; conditional_on_bank: boolean; raw_text: string | null;
  offer_valid_until: string | null;
}
export interface BankOfferRow {
  title: string; description: string | null; category: string | null; kind: string; bank: string | null; card_type: string | null;
  min_spend: string | null; max_discount: string | null; percent: string | null; estimated_saving: string | null;
  saving_note: string; terms_complete: boolean; you_hold_this_bank: boolean | null;
}
export interface VendorRow {
  mrp?: string | null; discount_pct?: string | null; emi_status?: string | null; emi_status_label?: string | null;
  bank_offers?: BankOfferRow[]; return_policy?: string | null;
  details?: { stock_units?: number; discount_label?: string; discount_ends_at?: string; cod_available?: boolean; product_expiry?: string };
  vendor: string; vendor_id?: string; status: string; source_url?: string; displayed_price?: string | null;
  effective_upfront_price?: string | null; availability?: string | null; seller?: string | null; delivery?: string | null;
  emi_options?: EmiRow[]; notes?: string[]; freshness?: Freshness | null; message?: string | null;
}
export interface OptionRow {
  vendor_id: string; vendor_name: string; kind: "upfront" | "emi"; total_cost: string | null; monthly_payment: string | null;
  tenure_months: number | null; total_interest: string | null; complete: boolean; conditional: boolean; notes: string[];
}
export interface VendorComparison {
  product_id: string; objective?: string; vendors: VendorRow[]; ranked_options?: OptionRow[];
  excluded_options?: { option: OptionRow; reason: string }[]; conditional_options?: OptionRow[];
  tenure_tradeoff?: { explanation: string; rows: Record<string, string>[]; annual_rate_pct: string } | null;
  data_issues?: { vendor: string; message: string }[]; disclaimer?: string; message?: string; emi_summary?: string | null;
}

export interface ReviewBlock {
  aggregate_rating: number | null; rating_scale: number | null; rating_count: number | null; reviews_analysed: number;
  source_url: string; fetched_at: string; review_date_range?: string[] | null;
  positive_themes?: { aspect: string; mentions: number; avg_sentiment: number }[];
  negative_themes?: { aspect: string; mentions: number; avg_sentiment: number }[];
  aspect_sentiment?: Record<string, { mentions: number; avg: number }>;
  excerpts?: ReviewExcerpt[]; flagged_examples?: ReviewExcerpt[]; limitations: string[]; method: string;
  integrity?: ReviewIntegrity;
}
export interface ReviewExcerpt {
  text: string; rating: number | null; date: string | null; verified?: boolean | null; helpful_votes?: number | null;
  trust?: "likely_genuine" | "uncertain" | "suspicious"; trust_score?: number; reasons?: string[]; boosts?: string[];
}
export interface ReviewIntegrity {
  integrity_score: number | null; integrity_label: "organic" | "mixed" | "doubtful" | null; confidence: "none" | "low" | "medium" | "high";
  distinct_reviews: number; counted: number; other_variant_reviews: number; likely_genuine: number; uncertain: number; suspicious: number;
  verified_share: number | null; flag_counts: Record<string, number>; sample_avg_rating: number | null; trust_weighted_rating: number | null;
  sample_sentiment: number | null; trust_weighted_sentiment: number | null; page_rating: number | null;
  rating_distribution: Record<string, number> | null; vendor_flags: string[]; limitations: string[]; method: string;
  growth: { status: string; snapshots: number; ratings_per_day?: number; growth_pct_per_month?: number; flags: string[] };
}
export interface SellerReputation {
  seller_id: number; seller_name: string | null; marketplace: string; is_active: boolean; observations: number; status: string;
  rating: number | null; ratings: number | null; positive_pct: number | null; ratings_per_day: number | null;
  positive_pct_change?: number | null; flags: string[];
}
export interface VendorReviews { vendor: string; product: ReviewBlock | null; seller: ReviewBlock | null; status: string; freshness: Freshness | null; message: string | null }

// ---------------------------------------------------------------- accounts & history
export interface Profile {
  user_id: string; email: string | null; display_name: string | null; role: "user" | "admin";
  is_disabled: boolean; created_at: string; last_seen_at: string;
}
export type ActivityKind = "sign_in" | "assistant_chat" | "recommendation" | "notifications_viewed" | "target_saved" | "admin_action";
export interface Activity {
  id: number; user_id: string; kind: ActivityKind; product_id: string | null; summary: string;
  payload: Record<string, unknown>; created_at: string; email?: string | null; display_name?: string | null;
}
export interface ChatSession {
  id: string; user_id: string; title: string; product_id: string | null; created_at: string; updated_at: string;
  questions?: number; last_question?: string | null;
}
export interface ChatMessageRow {
  id: number; request_id: string; role: "user" | "assistant"; content: string;
  status: "pending" | "complete" | "failed"; meta: { llm_used?: boolean; vendor_data_accessed?: boolean; recommendation?: Recommendation; error?: string };
  created_at: string;
}
export interface UserTarget { product_id: string; target_price: string | null; max_budget: string | null; alerts_enabled: boolean }

// Seller discovery (GET /api/products/{id}/sellers, never fetches)
export interface MarketplaceSeller {
  id: number; seller_key: string; seller_name: string | null; seller_url: string | null; marketplace: string; marketplace_url: string;
  is_active: boolean; first_seen_at: string; last_seen_at: string; last_in_stock_at: string | null; deactivation_reason: string | null;
  observed_at: string | null; price: string | null; mrp: string | null; in_stock: boolean | null; fulfilled_by: string | null;
  min_quantity: number | null; seller_rating: string | null; seller_rating_count: number | null; seller_positive_pct: string | null;
}
export interface SellerList {
  product_id: string; sellers: MarketplaceSeller[]; last_checked_at: string | null;
  discovered_sites: { id: number; vendor_name: string; source_url: string; origin: string; first_seen_at: string; last_checked_at: string | null; last_in_stock_at: string | null }[];
}
