from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Optional

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import sessionmaker
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


# ── Shared pipeline state ────────────────────────────────────────────────────
# Each parallel branch writes to its own key, so there are no merge conflicts.
# `errors` uses operator.add so both parallel branches' errors are accumulated.

class EmailPipelineState(TypedDict):
    email_id: str
    user_id: str
    trace_id: str
    session_factory: Any
    category_catalog_override: Optional[list]
    classifier_output: Optional[dict]
    relationship_output: Optional[dict]
    schedule_output: Optional[dict]
    response_output: Optional[dict]
    draft_output: Optional[dict]
    errors: Annotated[list[dict], operator.add]


# ── Node functions ────────────────────────────────────────────────────────────
# Each node imports lazily to avoid circular imports at module load time.

def _node_classifier(state: EmailPipelineState) -> dict:
    from services.orchestration import execute_classifier
    try:
        output = execute_classifier(
            state["session_factory"],
            trace_id=state["trace_id"],
            email_id=state["email_id"],
            user_id=state["user_id"],
            category_catalog_override=state.get("category_catalog_override"),
        )
        return {"classifier_output": output, "errors": []}
    except Exception as exc:
        logger.error("classifier node failed: %s", exc)
        return {"classifier_output": None, "errors": [{"step": "classifier", "error": str(exc)}]}


def _node_relationship_graph(state: EmailPipelineState) -> dict:
    from services.orchestration import execute_relationship_graph
    try:
        output = execute_relationship_graph(
            state["session_factory"],
            trace_id=state["trace_id"],
            email_id=state["email_id"],
            user_id=state["user_id"],
        )
        return {"relationship_output": output, "errors": []}
    except Exception as exc:
        logger.error("relationship_graph node failed: %s", exc)
        return {"relationship_output": None, "errors": [{"step": "relationship_graph", "error": str(exc)}]}


def _node_schedule(state: EmailPipelineState) -> dict:
    from services.orchestration import execute_schedule
    try:
        output = execute_schedule(
            state["session_factory"],
            trace_id=state["trace_id"],
            email_id=state["email_id"],
            user_id=state["user_id"],
        )
        return {"schedule_output": output, "errors": []}
    except Exception as exc:
        logger.error("schedule node failed: %s", exc)
        return {"schedule_output": None, "errors": [{"step": "schedule", "error": str(exc)}]}


def _node_response(state: EmailPipelineState) -> dict:
    from services.orchestration import execute_response
    try:
        output = execute_response(
            state["session_factory"],
            trace_id=state["trace_id"],
            email_id=state["email_id"],
            user_id=state["user_id"],
        )
        return {"response_output": output, "errors": []}
    except Exception as exc:
        logger.error("response node failed: %s", exc)
        return {"response_output": None, "errors": [{"step": "response", "error": str(exc)}]}


def _node_draft(state: EmailPipelineState) -> dict:
    from services.orchestration import maybe_create_reply_draft
    try:
        output = maybe_create_reply_draft(
            state["session_factory"],
            user_id=state["user_id"],
            email_id=state["email_id"],
        )
        return {"draft_output": output, "errors": []}
    except Exception as exc:
        logger.error("draft node failed: %s", exc)
        return {"draft_output": None, "errors": [{"step": "draft", "error": str(exc)}]}


# ── Graph builders ────────────────────────────────────────────────────────────

def _build_live_graph():
    """
    Live pipeline graph:

        START → classifier ─┬─→ relationship_graph ─┐
                            └─→ schedule            ─┴─→ response → draft → END

    relationship_graph and schedule execute in parallel (LangGraph fan-out).
    response waits for both branches to complete (fan-in).
    """
    g = StateGraph(EmailPipelineState)

    g.add_node("classifier", _node_classifier)
    g.add_node("relationship_graph", _node_relationship_graph)
    g.add_node("schedule", _node_schedule)
    g.add_node("response", _node_response)
    g.add_node("draft", _node_draft)

    g.add_edge(START, "classifier")
    # Fan-out: classifier → two parallel branches
    g.add_edge("classifier", "relationship_graph")
    g.add_edge("classifier", "schedule")
    # Fan-in: both branches must complete before response
    g.add_edge("relationship_graph", "response")
    g.add_edge("schedule", "response")
    g.add_edge("response", "draft")
    g.add_edge("draft", END)

    return g.compile()


def _build_historical_graph():
    """
    Historical (bootstrap) pipeline — no response/draft steps.

        START → classifier ─┬─→ relationship_graph ─→ END
    """
    g = StateGraph(EmailPipelineState)

    g.add_node("classifier", _node_classifier)
    g.add_node("relationship_graph", _node_relationship_graph)

    g.add_edge(START, "classifier")
    g.add_edge("classifier", "relationship_graph")
    g.add_edge("relationship_graph", END)

    return g.compile()


# Compiled graphs are module-level singletons — built once, reused per call.
_live_graph = _build_live_graph()
_historical_graph = _build_historical_graph()


# ── Public API ────────────────────────────────────────────────────────────────

def _initial_state(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    category_catalog_override: Optional[list] = None,
) -> EmailPipelineState:
    return EmailPipelineState(
        email_id=email_id,
        user_id=user_id,
        trace_id=trace_id,
        session_factory=session_factory,
        category_catalog_override=category_catalog_override,
        classifier_output=None,
        relationship_output=None,
        schedule_output=None,
        response_output=None,
        draft_output=None,
        errors=[],
    )


def process_live_inbox_email_graph(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    category_catalog_override: Optional[list] = None,
) -> dict[str, Any]:
    state = _live_graph.invoke(
        _initial_state(
            session_factory,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            category_catalog_override=category_catalog_override,
        )
    )
    return {
        "classifier": state.get("classifier_output"),
        "relationship_graph": state.get("relationship_output"),
        "schedule": state.get("schedule_output"),
        "response": state.get("response_output"),
        "draft_write": state.get("draft_output"),
        "errors": state.get("errors", []),
    }


def process_historical_inbox_email_graph(
    session_factory: sessionmaker,
    *,
    trace_id: str,
    email_id: str,
    user_id: str,
    category_catalog_override: Optional[list] = None,
) -> dict[str, Any]:
    state = _historical_graph.invoke(
        _initial_state(
            session_factory,
            trace_id=trace_id,
            email_id=email_id,
            user_id=user_id,
            category_catalog_override=category_catalog_override,
        )
    )
    return {
        "classifier": state.get("classifier_output"),
        "relationship_graph": state.get("relationship_output"),
        "errors": state.get("errors", []),
    }
