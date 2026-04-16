export type SummaryCard = {
  key: string;
  label: string;
  value: number;
  subtitle?: string | null;
};

export type CategorySuggestion = {
  suggestion_id: string;
  user_id: string;
  category_name: string;
  category_description: string;
  supporting_email_ids: string[];
  supporting_subjects: string[];
  rationale_keywords: string[];
  status: "pending" | "accepted" | "rejected";
  sample_size: number;
  process_limit: number;
  created_from_email_id?: string | null;
  promoted_category_id?: string | null;
  decided_at_utc?: string | null;
  created_at_utc: string;
  updated_at_utc: string;
};

export type PendingReviewItem = {
  email_id: string;
  user_id: string;
  reply_suggestion_id?: number | null;
  subject?: string | null;
  sender_name?: string | null;
  sender_email?: string | null;
  received_at_utc: string;
  decision_reason?: string | null;
  draft_status: string;
};

export type DashboardSeriesItem = {
  label: string;
  value: number;
};

export type RelationshipInsight = {
  person_email: string;
  person_name?: string | null;
  person_role?: string | null;
  organisation_name?: string | null;
  observation_count: number;
  relationship_weight: number;
};

export type UserDashboard = {
  user_id: string;
  summary_cards: SummaryCard[];
  pending_review_items: PendingReviewItem[];
  pending_tag_suggestions: CategorySuggestion[];
  category_distribution: DashboardSeriesItem[];
  top_relationships: RelationshipInsight[];
  schedule_overview: {
    recent_written_count: number;
    current_suggest_only_count: number;
    proactive_candidate_count: number;
  };
  feedback_overview: {
    total_events: number;
    recent_events: number;
    signal_counts: Record<string, number>;
    preference_vector: Record<string, unknown>;
  };
  last_refreshed_at_utc: string;
};

export type ReplyReviewStatus = {
  email_id: string;
  user_id: string;
  reply_suggestion_id: number;
  reply_required: boolean;
  decision_reason?: string | null;
  tone_templates: Record<string, string>;
  review_required: boolean;
  pending_review: boolean;
  latest_draft_write?: {
    reply_suggestion_id?: number | null;
    draft_status: string;
    policy_name: string;
    outlook_draft_id?: string | null;
    outlook_web_link?: string | null;
    error_message?: string | null;
  } | null;
  email_subject?: string | null;
  email_sender_name?: string | null;
  email_sender_email?: string | null;
  email_body_preview?: string | null;
};

export type ReplyReviewResult = {
  email_id: string;
  user_id: string;
  reply_suggestion_id: number;
  action: "approve" | "reject" | "defer";
  feedback_signal: string;
  draft_status: string;
  policy_name: string;
  outlook_draft_id?: string | null;
  outlook_web_link?: string | null;
  error_message?: string | null;
  pending_review: boolean;
  preference_vector: Record<string, unknown>;
};

export type ScheduleCandidate = {
  candidate_id: string;
  email_id: string;
  title: string;
  start_time_utc: string;
  end_time_utc: string;
  source_timezone: string;
  is_all_day: boolean;
  location?: string | null;
  confidence: number;
  conflict_score: number;
  action: string;
  write_status: string;
  outlook_event_id?: string | null;
  outlook_weblink?: string | null;
  email_subject?: string | null;
  email_sender_name?: string | null;
  email_sender_email?: string | null;
  email_received_at_utc?: string | null;
  classifier_summary?: string | null;
  classifier_category?: string | null;
  classifier_urgency_score?: number | null;
};

export type UserStatus = {
  user_id: string;
  primary_email?: string | null;
  display_name?: string | null;
  mailbox_connected: boolean;
  bootstrap_status: "pending" | "running" | "completed" | "failed";
  bootstrap_started_at_utc?: string | null;
  bootstrap_completed_at_utc?: string | null;
  bootstrap_error?: string | null;
  polling_enabled: boolean;
  last_poll_at_utc?: string | null;
  active_mode: string;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function retryBootstrap(userId: string) {
  return request<UserStatus>(`/v2/users/${userId}/bootstrap/retry`, { method: "POST" });
}

export function getMicrosoftAuthUrl() {
  return request<{ authorize_url: string; state: string }>("/auth/microsoft/start");
}

export function getUserStatus(userId: string) {
  return request<UserStatus>(`/v2/users/${userId}/status`);
}

export function getDashboard(userId: string) {
  return request<UserDashboard>(`/v2/users/${userId}/dashboard`);
}

export function getTagSuggestions(userId: string) {
  return request<{ user_id: string; suggestions: CategorySuggestion[] }>(
    `/v2/agents/classifier/tag_suggestions/${userId}`,
  );
}

export function decideTagSuggestion(suggestionId: string, action: "accept" | "reject") {
  return request<{ user_id: string; suggestion: CategorySuggestion; backfill: Record<string, unknown> }>(
    `/v2/agents/classifier/tag_suggestions/${suggestionId}`,
    {
      method: "POST",
      body: JSON.stringify({ action }),
    },
  );
}

export function refreshTagSuggestions(userId: string, sampleSize = 50, processLimit = 50) {
  return request<{
    status: string;
    reason?: string | null;
    user_id: string;
    generated_count: number;
    suggestions: CategorySuggestion[];
  }>(`/v2/n8n/generate_tag_suggestions/${userId}`, {
    method: "POST",
    body: JSON.stringify({ sample_size: sampleSize, process_limit: processLimit }),
  });
}

export function getScheduleCandidates(userId: string) {
  return request<{ user_id: string; candidates: ScheduleCandidate[] }>(
    `/v2/agents/schedule/candidates/${userId}`,
  );
}

export function submitScheduleReview(candidateId: string, action: "accept" | "reject" | "defer") {
  return request<{
    candidate_id: string;
    action: string;
    feedback_signal: string;
    write_status: string | null;
    outlook_event_id: string | null;
    outlook_weblink: string | null;
  }>(
    `/v2/agents/schedule/candidates/${candidateId}/review`,
    { method: "POST", body: JSON.stringify({ action }) },
  );
}

export function getReplyReviewStatus(emailId: string) {
  return request<ReplyReviewStatus>(`/v2/agents/response/review/${emailId}`);
}

export function submitReplyReview(
  emailId: string,
  body: {
    reply_suggestion_id: number;
    action: "approve" | "reject" | "defer";
    tone_key?: string;
    edited_body?: string;
  },
) {
  return request<ReplyReviewResult>(`/v2/agents/response/review/${emailId}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
