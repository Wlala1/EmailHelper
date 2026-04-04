import { FormEvent, useEffect, useState } from "react";

import {
  CategorySuggestion,
  PendingReviewItem,
  ReplyReviewStatus,
  UserDashboard,
  decideTagSuggestion,
  getDashboard,
  getReplyReviewStatus,
  getTagSuggestions,
  refreshTagSuggestions,
  submitReplyReview,
} from "./api";

type ViewKey = "overview" | "review" | "tags" | "insights";

const USER_ID_STORAGE_KEY = "ouma-demo-user-id";

function formatDate(value?: string | null) {
  if (!value) {
    return "N/A";
  }
  return new Date(value).toLocaleString();
}

function App() {
  const [view, setView] = useState<ViewKey>("overview");
  const [userIdInput, setUserIdInput] = useState(() => localStorage.getItem(USER_ID_STORAGE_KEY) || "");
  const [activeUserId, setActiveUserId] = useState(() => localStorage.getItem(USER_ID_STORAGE_KEY) || "");
  const [dashboard, setDashboard] = useState<UserDashboard | null>(null);
  const [suggestions, setSuggestions] = useState<CategorySuggestion[]>([]);
  const [selectedReviewItem, setSelectedReviewItem] = useState<PendingReviewItem | null>(null);
  const [reviewStatus, setReviewStatus] = useState<ReplyReviewStatus | null>(null);
  const [selectedTone, setSelectedTone] = useState<string>("professional");
  const [editedBody, setEditedBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");

  async function loadDashboard(targetUserId: string) {
    if (!targetUserId.trim()) {
      setError("Enter a user_id before loading the demo console.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [dashboardData, suggestionData] = await Promise.all([
        getDashboard(targetUserId),
        getTagSuggestions(targetUserId),
      ]);
      setDashboard(dashboardData);
      setSuggestions(suggestionData.suggestions);
      const firstPending = dashboardData.pending_review_items[0] || null;
      setSelectedReviewItem(firstPending);
      if (firstPending) {
        const detail = await getReplyReviewStatus(firstPending.email_id);
        setReviewStatus(detail);
        const initialTone = Object.keys(detail.tone_templates)[0] || "professional";
        setSelectedTone(initialTone);
        setEditedBody(detail.tone_templates[initialTone] || "");
      } else {
        setReviewStatus(null);
        setEditedBody("");
      }
      setActiveUserId(targetUserId);
      localStorage.setItem(USER_ID_STORAGE_KEY, targetUserId);
      setMessage("Dashboard refreshed.");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (activeUserId) {
      void loadDashboard(activeUserId);
    }
  }, []);

  useEffect(() => {
    if (!selectedReviewItem) {
      return;
    }
    const emailId = selectedReviewItem.email_id;
    let cancelled = false;
    async function loadReviewDetail() {
      setLoading(true);
      setError("");
      try {
        const detail = await getReplyReviewStatus(emailId);
        if (cancelled) {
          return;
        }
        setReviewStatus(detail);
        const toneKeys = Object.keys(detail.tone_templates);
        const nextTone = toneKeys.includes(selectedTone) ? selectedTone : toneKeys[0] || "professional";
        setSelectedTone(nextTone);
        setEditedBody(detail.tone_templates[nextTone] || "");
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load review detail");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadReviewDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedReviewItem?.email_id]);

  function handleLoad(event: FormEvent) {
    event.preventDefault();
    void loadDashboard(userIdInput.trim());
  }

  async function handleRefreshSuggestions() {
    if (!activeUserId) {
      setError("Load a user dashboard first.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await refreshTagSuggestions(activeUserId);
      setMessage(
        result.generated_count > 0
          ? `Generated ${result.generated_count} pending tag suggestion(s).`
          : result.reason || "No new tag suggestions were generated.",
      );
      await loadDashboard(activeUserId);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "Failed to refresh suggestions");
      setLoading(false);
    }
  }

  async function handleSuggestionDecision(suggestionId: string, action: "accept" | "reject") {
    setLoading(true);
    setError("");
    try {
      const result = await decideTagSuggestion(suggestionId, action);
      const backfillResult = result.backfill?.status ? ` Backfill: ${String(result.backfill.status)}.` : "";
      setMessage(`Suggestion ${action}ed.${backfillResult}`);
      if (activeUserId) {
        await loadDashboard(activeUserId);
      }
    } catch (decisionError) {
      setError(decisionError instanceof Error ? decisionError.message : "Failed to update suggestion");
      setLoading(false);
    }
  }

  async function handleReviewAction(action: "approve" | "reject" | "defer") {
    if (!selectedReviewItem || !reviewStatus) {
      setError("Select a pending review item first.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await submitReplyReview(selectedReviewItem.email_id, {
        reply_suggestion_id: reviewStatus.reply_suggestion_id,
        action,
        tone_key: action === "approve" ? selectedTone : undefined,
        edited_body: action === "approve" ? editedBody : undefined,
      });
      setMessage(`Review action recorded: ${response.draft_status}.`);
      if (activeUserId) {
        await loadDashboard(activeUserId);
      }
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "Failed to submit review");
      setLoading(false);
    }
  }

  const activeToneTemplate = reviewStatus?.tone_templates[selectedTone] || "";

  return (
    <div className="app-shell">
      <div className="aurora aurora-a" />
      <div className="aurora aurora-b" />
      <header className="hero">
        <div>
          <p className="eyebrow">OUMA Demo Console</p>
          <h1>Proposal-aligned review, tagging, and insight cockpit</h1>
          <p className="hero-copy">
            n8n remains the system-level orchestrator. This console exposes the human review queue,
            tag suggestion workflow, and the insight surfaces that were missing from the PDF demo story.
          </p>
        </div>
        <form className="user-bar" onSubmit={handleLoad}>
          <label>
            Demo User ID
            <input
              value={userIdInput}
              onChange={(event) => setUserIdInput(event.target.value)}
              placeholder="student-demo"
            />
          </label>
          <button type="submit" disabled={loading}>
            Load Console
          </button>
          <button type="button" className="secondary" onClick={() => void handleRefreshSuggestions()} disabled={loading}>
            Refresh Tag Suggestions
          </button>
        </form>
        <div className="status-strip">
          <span>{loading ? "Loading..." : message || "Ready"}</span>
          {error ? <span className="error-text">{error}</span> : null}
        </div>
      </header>

      <nav className="nav-tabs">
        {[
          ["overview", "Overview"],
          ["review", "Review Queue"],
          ["tags", "Tag Suggestions"],
          ["insights", "Insights"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={view === key ? "active" : ""}
            onClick={() => setView(key as ViewKey)}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="content-grid">
        {view === "overview" ? (
          <section className="panel">
            <div className="section-heading">
              <h2>Overview</h2>
              <p>Last refreshed: {formatDate(dashboard?.last_refreshed_at_utc)}</p>
            </div>
            <div className="card-grid">
              {(dashboard?.summary_cards || []).map((card) => (
                <article key={card.key} className="metric-card">
                  <p>{card.label}</p>
                  <strong>{card.value}</strong>
                  <span>{card.subtitle || ""}</span>
                </article>
              ))}
            </div>
            <div className="pipeline-panel">
              <div className="pipeline-node">Email Intake</div>
              <div className="pipeline-arrow">→</div>
              <div className="pipeline-node">Classifier</div>
              <div className="pipeline-arrow">→</div>
              <div className="pipeline-node">Attachment | Relationship | Schedule</div>
              <div className="pipeline-arrow">→</div>
              <div className="pipeline-node">Response + Human Review</div>
            </div>
            <div className="overview-columns">
              <article className="mini-panel">
                <h3>Pending Reply Queue</h3>
                {(dashboard?.pending_review_items || []).map((item) => (
                  <div key={item.email_id} className="list-row">
                    <div>
                      <strong>{item.subject || "(No Subject)"}</strong>
                      <p>{item.sender_name || item.sender_email}</p>
                    </div>
                    <span>{formatDate(item.received_at_utc)}</span>
                  </div>
                ))}
              </article>
              <article className="mini-panel">
                <h3>Pending Tag Suggestions</h3>
                {suggestions.slice(0, 4).map((item) => (
                  <div key={item.suggestion_id} className="tag-card compact">
                    <strong>{item.category_name}</strong>
                    <p>{item.category_description}</p>
                  </div>
                ))}
              </article>
            </div>
          </section>
        ) : null}

        {view === "review" ? (
          <section className="panel split-panel">
            <div className="queue-column">
              <div className="section-heading">
                <h2>Review Queue</h2>
                <p>Reply drafts require human approval before Outlook draft write-back.</p>
              </div>
              {(dashboard?.pending_review_items || []).length === 0 ? (
                <div className="empty-state">No pending review items for this user.</div>
              ) : (
                (dashboard?.pending_review_items || []).map((item) => (
                  <button
                    type="button"
                    key={item.email_id}
                    className={`queue-item ${selectedReviewItem?.email_id === item.email_id ? "selected" : ""}`}
                    onClick={() => setSelectedReviewItem(item)}
                  >
                    <strong>{item.subject || "(No Subject)"}</strong>
                    <span>{item.sender_name || item.sender_email}</span>
                    <small>{formatDate(item.received_at_utc)}</small>
                  </button>
                ))
              )}
            </div>
            <div className="detail-column">
              {reviewStatus ? (
                <>
                  <div className="section-heading">
                    <h2>{selectedReviewItem?.subject || "Review Detail"}</h2>
                    <p>{reviewStatus.decision_reason || "No decision reason available."}</p>
                  </div>
                  <div className="tone-switcher">
                    {Object.entries(reviewStatus.tone_templates).map(([key, value]) => (
                      <button
                        key={key}
                        type="button"
                        className={selectedTone === key ? "active" : ""}
                        onClick={() => {
                          setSelectedTone(key);
                          setEditedBody(value);
                        }}
                      >
                        {key}
                      </button>
                    ))}
                  </div>
                  <textarea
                    value={editedBody}
                    onChange={(event) => setEditedBody(event.target.value)}
                    rows={14}
                  />
                  <div className="action-row">
                    <button type="button" onClick={() => void handleReviewAction("approve")} disabled={loading}>
                      Approve Draft
                    </button>
                    <button type="button" className="secondary" onClick={() => void handleReviewAction("defer")} disabled={loading}>
                      Defer
                    </button>
                    <button type="button" className="danger" onClick={() => void handleReviewAction("reject")} disabled={loading}>
                      Reject
                    </button>
                  </div>
                </>
              ) : (
                <div className="empty-state">Select a queue item to inspect tone templates and draft content.</div>
              )}
            </div>
          </section>
        ) : null}

        {view === "tags" ? (
          <section className="panel">
            <div className="section-heading">
              <h2>Tag Suggestions</h2>
              <p>Human-in-the-loop category proposals grounded in backlog email samples.</p>
            </div>
            <div className="suggestion-grid">
              {suggestions.length === 0 ? (
                <div className="empty-state">No tag suggestions yet. Run the refresh action to generate them.</div>
              ) : (
                suggestions.map((suggestion) => (
                  <article key={suggestion.suggestion_id} className={`tag-card ${suggestion.status}`}>
                    <div className="tag-head">
                      <div>
                        <p className="status-badge">{suggestion.status}</p>
                        <h3>{suggestion.category_name}</h3>
                      </div>
                      <div className="action-stack">
                        <button
                          type="button"
                          onClick={() => void handleSuggestionDecision(suggestion.suggestion_id, "accept")}
                          disabled={loading || suggestion.status === "accepted"}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void handleSuggestionDecision(suggestion.suggestion_id, "reject")}
                          disabled={loading || suggestion.status === "rejected"}
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                    <p className="tag-description">{suggestion.category_description}</p>
                    <div className="keyword-cloud">
                      {suggestion.rationale_keywords.map((keyword) => (
                        <span key={keyword}>{keyword}</span>
                      ))}
                    </div>
                    <div className="support-list">
                      <h4>Supporting Subjects</h4>
                      {suggestion.supporting_subjects.map((subject) => (
                        <div key={subject} className="support-item">
                          {subject}
                        </div>
                      ))}
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
        ) : null}

        {view === "insights" ? (
          <section className="panel">
            <div className="section-heading">
              <h2>Insights</h2>
              <p>Classifier distribution, relationship graph summary, schedule signals, and preference learning.</p>
            </div>
            <div className="insight-columns">
              <article className="mini-panel">
                <h3>Category Distribution</h3>
                {(dashboard?.category_distribution || []).map((item) => (
                  <div key={item.label} className="bar-row">
                    <span>{item.label}</span>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${Math.min(100, item.value * 12)}%` }} />
                    </div>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </article>
              <article className="mini-panel">
                <h3>Relationship Highlights</h3>
                {(dashboard?.top_relationships || []).map((item) => (
                  <div key={item.person_email} className="relationship-item">
                    <div>
                      <strong>{item.person_name || item.person_email}</strong>
                      <p>
                        {item.person_role || "Unknown role"}
                        {item.organisation_name ? ` · ${item.organisation_name}` : ""}
                      </p>
                    </div>
                    <span>{item.relationship_weight.toFixed(2)}</span>
                  </div>
                ))}
              </article>
            </div>
            <div className="insight-columns">
              <article className="mini-panel">
                <h3>Schedule Overview</h3>
                <div className="list-row">
                  <span>Recent tentative events written</span>
                  <strong>{dashboard?.schedule_overview.recent_written_count || 0}</strong>
                </div>
                <div className="list-row">
                  <span>Current suggest-only candidates</span>
                  <strong>{dashboard?.schedule_overview.current_suggest_only_count || 0}</strong>
                </div>
                <div className="list-row">
                  <span>Proactive candidates</span>
                  <strong>{dashboard?.schedule_overview.proactive_candidate_count || 0}</strong>
                </div>
              </article>
              <article className="mini-panel">
                <h3>Preference Learning</h3>
                <div className="list-row">
                  <span>Total feedback events</span>
                  <strong>{dashboard?.feedback_overview.total_events || 0}</strong>
                </div>
                <div className="list-row">
                  <span>Recent feedback events</span>
                  <strong>{dashboard?.feedback_overview.recent_events || 0}</strong>
                </div>
                {Object.entries(dashboard?.feedback_overview.signal_counts || {}).map(([signal, count]) => (
                  <div key={signal} className="list-row">
                    <span>{signal}</span>
                    <strong>{count}</strong>
                  </div>
                ))}
              </article>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}

export default App;
