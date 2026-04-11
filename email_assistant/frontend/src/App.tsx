import { FormEvent, useEffect, useRef, useState } from "react";

import {
  CategorySuggestion,
  PendingReviewItem,
  ReplyReviewStatus,
  UserDashboard,
  UserStatus,
  decideTagSuggestion,
  getDashboard,
  getMicrosoftAuthUrl,
  getReplyReviewStatus,
  getTagSuggestions,
  getUserStatus,
  refreshTagSuggestions,
  retryBootstrap,
  submitReplyReview,
} from "./api";
import RelationshipGraph from "./RelationshipGraph";

type AppState = "login" | "bootstrapping" | "dashboard";
type ViewKey = "overview" | "review" | "tags" | "insights";

const USER_ID_KEY = "ouma-user-id";

function formatDate(value?: string | null) {
  if (!value) return "N/A";
  return new Date(value).toLocaleString();
}

// ─── Login page ──────────────────────────────────────────────────────────────

function LoginPage({ onLogin, loading, error }: { onLogin: () => void; loading: boolean; error: string }) {
  return (
    <div className="login-page">
      <div className="login-card">
        <p className="eyebrow">OUMA</p>
        <h1>Outlook Unified Multi-Agent</h1>
        <p className="login-description">
          Connect your Outlook mailbox to classify emails, build your relationship graph, and get
          context-aware reply suggestions.
        </p>
        <button className="ms-login-btn" onClick={onLogin} disabled={loading}>
          {loading ? "Redirecting…" : "Sign in with Microsoft"}
        </button>
        {error ? <p className="error-text">{error}</p> : null}
      </div>
    </div>
  );
}

// ─── Bootstrapping page ───────────────────────────────────────────────────────

function BootstrappingPage({
  status,
  error,
  onRetry,
}: {
  status: UserStatus | null;
  error: string;
  onRetry: () => void;
}) {
  const failed = status?.bootstrap_status === "failed";
  return (
    <div className="login-page">
      <div className="login-card">
        <p className="eyebrow">OUMA</p>
        {failed ? (
          <>
            <h2>Sync failed</h2>
            <p className="error-text">{status?.bootstrap_error || error || "Unknown error"}</p>
            <button className="ms-login-btn" onClick={onRetry}>
              Retry
            </button>
          </>
        ) : (
          <>
            <div className="bootstrap-spinner large" />
            <h2>Syncing your mailbox…</h2>
            {status && (
              <p className="login-description">
                Signed in as <strong>{status.display_name || status.primary_email}</strong>
                <br />
                Importing up to 180 days of email history. This may take a few minutes.
              </p>
            )}
            <p className="muted-label">
              Started {formatDate(status?.bootstrap_started_at_utc)}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────

function App() {
  const [appState, setAppState] = useState<AppState>("login");
  const [activeUserId, setActiveUserId] = useState("");
  const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
  const [view, setView] = useState<ViewKey>("overview");
  const [dashboard, setDashboard] = useState<UserDashboard | null>(null);
  const [suggestions, setSuggestions] = useState<CategorySuggestion[]>([]);
  const [selectedReviewItem, setSelectedReviewItem] = useState<PendingReviewItem | null>(null);
  const [reviewStatus, setReviewStatus] = useState<ReplyReviewStatus | null>(null);
  const [selectedTone, setSelectedTone] = useState("professional");
  const [editedBody, setEditedBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Helpers ────────────────────────────────────────────────────────────────

  async function loadDashboard(uid: string) {
    setLoading(true);
    setError("");
    try {
      const [dashData, suggestData] = await Promise.all([
        getDashboard(uid),
        getTagSuggestions(uid),
      ]);
      setDashboard(dashData);
      setSuggestions(suggestData.suggestions);
      const first = dashData.pending_review_items[0] ?? null;
      setSelectedReviewItem(first);
      if (first) {
        const detail = await getReplyReviewStatus(first.email_id);
        setReviewStatus(detail);
        const tone = Object.keys(detail.tone_templates)[0] ?? "professional";
        setSelectedTone(tone);
        setEditedBody(detail.tone_templates[tone] ?? "");
      } else {
        setReviewStatus(null);
        setEditedBody("");
      }
      setMessage("Dashboard refreshed.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }

  function startBootstrapPoll(uid: string) {
    if (pollRef.current) clearTimeout(pollRef.current);
    const poll = async () => {
      try {
        const status = await getUserStatus(uid);
        setUserStatus(status);
        if (status.bootstrap_status === "completed") {
          setAppState("dashboard");
          await loadDashboard(uid);
        } else if (status.bootstrap_status === "failed") {
          // stay on bootstrapping page, show error
        } else {
          pollRef.current = setTimeout(() => { void poll(); }, 5000);
        }
      } catch {
        pollRef.current = setTimeout(() => { void poll(); }, 5000);
      }
    };
    void poll();
  }

  async function resolveSession(uid: string, isNewConnection: boolean) {
    try {
      const status = await getUserStatus(uid);
      setUserStatus(status);
      setActiveUserId(uid);
      localStorage.setItem(USER_ID_KEY, uid);

      if (isNewConnection && status.bootstrap_status !== "completed") {
        setAppState("bootstrapping");
        startBootstrapPoll(uid);
      } else {
        setAppState("dashboard");
        await loadDashboard(uid);
      }
    } catch {
      // user not found or network error → back to login
      localStorage.removeItem(USER_ID_KEY);
      setAppState("login");
    }
  }

  // ── Mount: check URL params or localStorage ────────────────────────────────

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlUserId = params.get("user_id");
    const connected = params.get("connected") === "true";

    if (urlUserId) {
      window.history.replaceState({}, "", window.location.pathname);
      void resolveSession(urlUserId, connected);
    } else {
      const stored = localStorage.getItem(USER_ID_KEY);
      if (stored) {
        void resolveSession(stored, false);
      }
      // else: stay on login
    }

    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  // ── Load review detail when selection changes ──────────────────────────────

  useEffect(() => {
    if (!selectedReviewItem) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const detail = await getReplyReviewStatus(selectedReviewItem.email_id);
        if (cancelled) return;
        setReviewStatus(detail);
        const keys = Object.keys(detail.tone_templates);
        const tone = keys.includes(selectedTone) ? selectedTone : (keys[0] ?? "professional");
        setSelectedTone(tone);
        setEditedBody(detail.tone_templates[tone] ?? "");
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load review");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [selectedReviewItem?.email_id]);

  // ── Actions ────────────────────────────────────────────────────────────────

  async function handleMicrosoftLogin() {
    setLoading(true);
    setError("");
    try {
      const { authorize_url } = await getMicrosoftAuthUrl();
      window.location.href = authorize_url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start login");
      setLoading(false);
    }
  }

  function handleLogout() {
    if (pollRef.current) clearTimeout(pollRef.current);
    localStorage.removeItem(USER_ID_KEY);
    setActiveUserId("");
    setUserStatus(null);
    setDashboard(null);
    setSuggestions([]);
    setAppState("login");
  }

  async function handleRefreshSuggestions() {
    if (!activeUserId) return;
    setLoading(true);
    setError("");
    try {
      const result = await refreshTagSuggestions(activeUserId);
      setMessage(
        result.generated_count > 0
          ? `Generated ${result.generated_count} new tag suggestion(s).`
          : result.reason ?? "No new suggestions generated.",
      );
      await loadDashboard(activeUserId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh suggestions");
      setLoading(false);
    }
  }

  async function handleSuggestionDecision(id: string, action: "accept" | "reject") {
    setLoading(true);
    setError("");
    try {
      const result = await decideTagSuggestion(id, action);
      const extra = result.backfill?.status ? ` Backfill: ${String(result.backfill.status)}.` : "";
      setMessage(`Suggestion ${action}ed.${extra}`);
      await loadDashboard(activeUserId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update suggestion");
      setLoading(false);
    }
  }

  async function handleReviewAction(action: "approve" | "reject" | "defer") {
    if (!selectedReviewItem || !reviewStatus) return;
    setLoading(true);
    setError("");
    try {
      const res = await submitReplyReview(selectedReviewItem.email_id, {
        reply_suggestion_id: reviewStatus.reply_suggestion_id,
        action,
        tone_key: action === "approve" ? selectedTone : undefined,
        edited_body: action === "approve" ? editedBody : undefined,
      });
      setMessage(`Review action recorded: ${res.draft_status}.`);
      await loadDashboard(activeUserId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to submit review");
      setLoading(false);
    }
  }

  // ── Render branches ────────────────────────────────────────────────────────

  if (appState === "login") {
    return <LoginPage onLogin={() => void handleMicrosoftLogin()} loading={loading} error={error} />;
  }

  if (appState === "bootstrapping") {
    return (
      <BootstrappingPage
        status={userStatus}
        error={error}
        onRetry={async () => {
          if (!activeUserId) return;
          try {
            await retryBootstrap(activeUserId);
            startBootstrapPoll(activeUserId);
          } catch (e) {
            setError(e instanceof Error ? e.message : "Retry failed");
          }
        }}
      />
    );
  }

  // ── Dashboard ──────────────────────────────────────────────────────────────

  return (
    <div className="app-shell">
      <div className="aurora aurora-a" />
      <div className="aurora aurora-b" />

      <header className="hero">
        <div className="hero-left">
          <p className="eyebrow">OUMA</p>
          <h1>Outlook Unified Multi-Agent</h1>
        </div>
        <div className="hero-right">
          <div className="user-info">
            <span className="user-name">{userStatus?.display_name ?? activeUserId}</span>
            <span className="user-email">{userStatus?.primary_email}</span>
          </div>
          <div className="header-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => void handleRefreshSuggestions()}
              disabled={loading}
            >
              Refresh Tags
            </button>
            <button type="button" className="secondary" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </div>
        <div className="status-strip">
          <span>{loading ? "Loading…" : message || "Ready"}</span>
          {error ? <span className="error-text">{error}</span> : null}
        </div>
      </header>

      <nav className="nav-tabs">
        {(["overview", "review", "tags", "insights"] as ViewKey[]).map((key) => (
          <button
            key={key}
            type="button"
            className={view === key ? "active" : ""}
            onClick={() => setView(key)}
          >
            {key === "overview" ? "Overview" : key === "review" ? "Review Queue" : key === "tags" ? "Tag Suggestions" : "Insights"}
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
              {(dashboard?.summary_cards ?? []).map((card) => (
                <article key={card.key} className="metric-card">
                  <p>{card.label}</p>
                  <strong>{card.value}</strong>
                  <span>{card.subtitle ?? ""}</span>
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
                {(dashboard?.pending_review_items ?? []).map((item) => (
                  <div key={item.email_id} className="list-row">
                    <div>
                      <strong>{item.subject ?? "(No Subject)"}</strong>
                      <p>{item.sender_name ?? item.sender_email}</p>
                    </div>
                    <span>{formatDate(item.received_at_utc)}</span>
                  </div>
                ))}
                {(dashboard?.pending_review_items ?? []).length === 0 && (
                  <div className="empty-state">No pending replies.</div>
                )}
              </article>
              <article className="mini-panel">
                <h3>Pending Tag Suggestions</h3>
                {suggestions.slice(0, 4).map((item) => (
                  <div key={item.suggestion_id} className="tag-card compact">
                    <strong>{item.category_name}</strong>
                    <p>{item.category_description}</p>
                  </div>
                ))}
                {suggestions.length === 0 && (
                  <div className="empty-state">No pending suggestions.</div>
                )}
              </article>
            </div>
          </section>
        ) : null}

        {view === "review" ? (
          <section className="panel split-panel">
            <div className="queue-column">
              <div className="section-heading">
                <h2>Review Queue</h2>
                <p>Reply drafts require approval before Outlook write-back.</p>
              </div>
              {(dashboard?.pending_review_items ?? []).length === 0 ? (
                <div className="empty-state">No pending review items.</div>
              ) : (
                (dashboard?.pending_review_items ?? []).map((item) => (
                  <button
                    type="button"
                    key={item.email_id}
                    className={`queue-item ${selectedReviewItem?.email_id === item.email_id ? "selected" : ""}`}
                    onClick={() => setSelectedReviewItem(item)}
                  >
                    <strong>{item.subject ?? "(No Subject)"}</strong>
                    <span>{item.sender_name ?? item.sender_email}</span>
                    <small>{formatDate(item.received_at_utc)}</small>
                  </button>
                ))
              )}
            </div>
            <div className="detail-column">
              {reviewStatus ? (
                <>
                  <div className="email-context-panel">
                    <div className="email-context-meta">
                      <span className="email-context-sender">
                        {reviewStatus.email_sender_name ?? reviewStatus.email_sender_email ?? selectedReviewItem?.sender_name ?? "Unknown sender"}
                        {reviewStatus.email_sender_name && reviewStatus.email_sender_email
                          ? <span className="email-context-addr"> &lt;{reviewStatus.email_sender_email}&gt;</span>
                          : null}
                      </span>
                      <span className="email-context-date">{formatDate(selectedReviewItem?.received_at_utc)}</span>
                    </div>
                    <h3 className="email-context-subject">{reviewStatus.email_subject ?? selectedReviewItem?.subject ?? "(No Subject)"}</h3>
                    {reviewStatus.email_body_preview && (
                      <p className="email-context-body">{reviewStatus.email_body_preview}</p>
                    )}
                    {reviewStatus.decision_reason && (
                      <div className="decision-reason-badge">
                        <span className="decision-reason-label">Why reply needed:</span> {reviewStatus.decision_reason}
                      </div>
                    )}
                  </div>
                  <div className="reply-draft-label">Suggested reply</div>
                  <div className="tone-switcher">
                    {Object.entries(reviewStatus.tone_templates).map(([key, value]) => (
                      <button
                        key={key}
                        type="button"
                        className={selectedTone === key ? "active" : ""}
                        onClick={() => { setSelectedTone(key); setEditedBody(value); }}
                      >
                        {key}
                      </button>
                    ))}
                  </div>
                  <textarea value={editedBody} onChange={(e) => setEditedBody(e.target.value)} rows={12} />
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
                <div className="empty-state">Select a queue item to inspect tone templates.</div>
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
                <div className="empty-state">No tag suggestions. Click "Refresh Tags" to generate.</div>
              ) : (
                suggestions.map((s) => (
                  <article key={s.suggestion_id} className={`tag-card ${s.status}`}>
                    <div className="tag-head">
                      <div>
                        <p className="status-badge">{s.status}</p>
                        <h3>{s.category_name}</h3>
                      </div>
                      <div className="action-stack">
                        <button
                          type="button"
                          onClick={() => void handleSuggestionDecision(s.suggestion_id, "accept")}
                          disabled={loading || s.status === "accepted"}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void handleSuggestionDecision(s.suggestion_id, "reject")}
                          disabled={loading || s.status === "rejected"}
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                    <p className="tag-description">{s.category_description}</p>
                    <div className="keyword-cloud">
                      {s.rationale_keywords.map((kw) => <span key={kw}>{kw}</span>)}
                    </div>
                    <div className="support-list">
                      <h4>Supporting Subjects</h4>
                      {s.supporting_subjects.map((subj) => (
                        <div key={subj} className="support-item">{subj}</div>
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
                {(dashboard?.category_distribution ?? []).map((item) => (
                  <div key={item.label} className="bar-row">
                    <span>{item.label}</span>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${Math.min(100, item.value * 12)}%` }} />
                    </div>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </article>
              <article className="mini-panel graph-panel">
                <h3>Relationship Graph</h3>
                <RelationshipGraph
                  relationships={dashboard?.top_relationships ?? []}
                  userEmail={userStatus?.primary_email ?? activeUserId}
                  width={640}
                  height={420}
                />
              </article>
            </div>
            <div className="insight-columns">
              <article className="mini-panel">
                <h3>Schedule Overview</h3>
                <div className="list-row">
                  <span>Recent tentative events written</span>
                  <strong>{dashboard?.schedule_overview.recent_written_count ?? 0}</strong>
                </div>
                <div className="list-row">
                  <span>Current suggest-only candidates</span>
                  <strong>{dashboard?.schedule_overview.current_suggest_only_count ?? 0}</strong>
                </div>
                <div className="list-row">
                  <span>Proactive candidates</span>
                  <strong>{dashboard?.schedule_overview.proactive_candidate_count ?? 0}</strong>
                </div>
              </article>
              <article className="mini-panel">
                <h3>Preference Learning</h3>
                <div className="list-row">
                  <span>Total feedback events</span>
                  <strong>{dashboard?.feedback_overview.total_events ?? 0}</strong>
                </div>
                <div className="list-row">
                  <span>Recent feedback events</span>
                  <strong>{dashboard?.feedback_overview.recent_events ?? 0}</strong>
                </div>
                {Object.entries(dashboard?.feedback_overview.signal_counts ?? {}).map(([sig, cnt]) => (
                  <div key={sig} className="list-row">
                    <span>{sig}</span>
                    <strong>{cnt}</strong>
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
