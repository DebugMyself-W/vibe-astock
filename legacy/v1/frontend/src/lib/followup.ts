export interface FollowupMember {
  code: string; name: string; boards?: number | null;
  pct?: number | null; price?: number | null; quote_time?: string | null;
  limit_up?: boolean | null; limit_down?: boolean | null; reason?: string;
}
export interface MemberStats {
  members: FollowupMember[]; sample: number; valid: number; coverage: number | null;
  median: number | null; up_count: number | null;
  limit_up_count: number | null; limit_down_count: number | null;
}
export interface FollowupDirection {
  id: string; direction: string; logic: string; risk: string;
  tags: string[]; complete: boolean; binding_reason: string; binding_source?: string;
  suggested_tags?: string[]; suggestion_source?: string;
  members: FollowupMember[]; explicit_members: FollowupMember[]; unresolved: string[];
  stats?: MemberStats; explicit?: MemberStats;
}
export interface FollowupItem {
  metric: string; label?: string; unit?: string; direction: string; reason: string;
  base_value?: number | null; eps?: number | null; current_value?: number | null;
  delta?: number | null; state?: string; reason_detail?: string;
  sample?: number | null; valid?: number | null; verified?: boolean | null;
}
export interface FollowupSnapshot {
  prediction_date: string; eval_date: string; observed_at: string;
  stage: "intraday" | "closing" | "final"; eligible: boolean; items: FollowupItem[];
  directions: FollowupDirection[];
  summary: { total: number; decided: number; hit: number; undecidable: number; rate: number | null };
}
export interface FollowupData {
  available: boolean; prediction_date: string; eval_date?: string | null;
  cutoff_at?: string | null; editable: boolean; eligible: boolean;
  prediction?: {
    recorded_at: string; version: string; items: FollowupItem[]; directions: FollowupDirection[];
    focus: { market_oneliner?: string; emotion_phase?: string; risk_alerts?: string[] };
    baseline_sample_date?: string | null;
    universe: { themes: { tag: string; complete: boolean; members: FollowupMember[] }[] };
  } | null;
  times: string[]; snapshot?: FollowupSnapshot | null; final?: FollowupSnapshot | null;
  status: { phase: string; enabled: boolean; delayed: boolean; error?: string;
    last_success_at?: string; last_attempt_at?: string };
  notes: { id: string; text: string; created_at: string }[];
}
