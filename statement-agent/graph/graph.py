"""LangGraph parse-graph builder.

The graph is linear: ``route -> extract -> validate -> persist -> finalize``.
The judge no longer runs inline on every parse — it is a post-hoc evaluation
that samples MLflow traces asynchronously (see ``judge/scorer.py``). The
``judge_node`` function stays in :mod:`graph.nodes` so the post-hoc scorer can
reuse the same scoring logic, but it is NOT wired into the compiled graph.

LangGraph's :class:`StateGraph` is used with a typed state object.
``langgraph`` is imported function-locally inside :func:`build_graph` so this
module (and the whole package) imports cleanly with stdlib only -- the
contract-test path never touches the builder, and the local environment (where
langgraph cannot be installed) can still import the package.

Routing: every node returns the same ``GraphState`` instance, so edges are
unconditional and ordered. A terminal ``outcome`` set by an earlier node
short-circuits downstream nodes (they no-op on a terminal state). This avoids
conditional edges entirely, which keeps the graph inspectable and the test
fakes simple.

Root span: :func:`run_graph` emits a root ``"parse"`` TraceEvent in a
``finally`` block after the graph completes (or raises). This is the root
event that :class:`harness.tracing.SpanTreeBuilder` flushes on — without it
the span tree never flushes and the MLflow run is never finalized. Child
events emitted by nodes via :func:`graph.nodes._trace` carry
``parent_span_id=f"{request_id}:parse"`` so the builder links them correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from contracts.models import TraceEvent
from graph.nodes import NodeDeps, finalize_node, extract_node, persist_node, route_node, validate_node
from graph.state import GraphState


_NODES = (
    ("route", route_node),
    ("extract", extract_node),
    ("validate", validate_node),
    ("persist", persist_node),
    ("finalize", finalize_node),
)


def build_graph(deps: NodeDeps) -> Any:
    """Build and compile the LangGraph parse graph.

    Raises :class:`RuntimeError` with a clear message if ``langgraph`` is not
    installed -- the caller is expected to either have it (deploy) or skip the
    graph-level test (local stdlib gate).
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - exercised only where langgraph absent
        raise RuntimeError(
            "langgraph is not installed; the parse graph cannot be built. "
            "Install langgraph (declared in requirements.txt) or run the stdlib gate."
        ) from exc

    builder: StateGraph = StateGraph(GraphState)
    for name, fn in _NODES:
        builder.add_node(name, lambda state, _fn=fn, _deps=deps: _fn(state, _deps))
    prev = START
    for name, _ in _NODES:
        builder.add_edge(prev, name)
        prev = name
    builder.add_edge(prev, END)
    return builder.compile()


def run_graph(deps: NodeDeps, state: GraphState) -> GraphState:
    """Execute the parse pipeline once, returning the final state.

    This is the convenience entry point used by the skill and the CLI. It runs
    each node in order — ``route -> extract -> validate -> persist -> finalize``
    — passing the *same* ``state`` object to every node so mutations persist.

    We execute nodes directly instead of calling ``graph.invoke()`` (the
    compiled LangGraph) because LangGraph's ``StateGraph.invoke()`` returns a
    plain ``dict`` — *not* the ``GraphState`` dataclass instance we passed in.
    LangGraph rebuilds fresh ``GraphState`` instances from its internal dict
    for each node call, so node mutations land on those copies and never reach
    the original ``state`` object. That left ``state.outcome`` as ``None`` (the
    SSE "complete" event carried ``outcome: null`` → frontend showed "UNKNOWN")
    and broke extraction-item streaming, since the progress sink holds a ref to
    the original ``state`` and could never see ``state.extraction``. The graph is
    linear with no conditional edges, parallelism, or checkpointing, so direct
    execution is functionally identical to the compiled graph — but the shared
    ``state`` object keeps all mutations. ``build_graph()`` is retained for
    future LangGraph integration and separate compiled-graph testing.

    After the pipeline completes (or raises), a root ``"parse"`` TraceEvent is
    emitted in a ``finally`` block. This is the root event that
    :class:`harness.tracing.SpanTreeBuilder` flushes on — without it the span
    tree never flushes, ``_end_run()`` is never called, and the MLflow run
    stays ``RUNNING`` forever (resource leak). Child events emitted by nodes
    via :func:`graph.nodes._trace` carry ``parent_span_id=f"{request_id}:parse"``
    so the builder links them as children of this root.
    """
    start = datetime.now(UTC)
    graph_error: str | None = None
    try:
        # Run nodes directly rather than via graph.invoke(). See the docstring
        # for why: LangGraph returns a dict, not our GraphState instance, so
        # node mutations never reach the original state object we hold a ref to.
        for _name, fn in _NODES:
            fn(state, deps)
        return state
    except Exception as exc:
        graph_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if deps.trace_sink is not None:
            try:
                deps.trace_sink.record(TraceEvent(
                    request_id=state.request_id,
                    name="parse",
                    started_at=start,
                    ended_at=datetime.now(UTC),
                    attributes=state.as_summary(),
                    error=graph_error,
                    span_id=f"{state.request_id}:parse",
                    parent_span_id=None,
                ))
            except Exception:
                pass  # telemetry must never block the result or re-raise
