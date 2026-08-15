"""LangGraph parse-graph builder.

The graph is linear: ``route -> extract -> validate -> persist -> judge ->
finalize``. LangGraph's :class:`StateGraph` is used with a typed state object.
``langgraph`` is imported function-locally inside :func:`build_graph` so this
module (and the whole package) imports cleanly with stdlib only -- the
contract-test path never touches the builder, and the local environment (where
langgraph cannot be installed) can still import the package.

Routing: every node returns the same ``GraphState`` instance, so edges are
unconditional and ordered. A terminal ``outcome`` set by an earlier node
short-circuits downstream nodes (they no-op on a terminal state). This avoids
conditional edges entirely, which keeps the graph inspectable and the test
fakes simple.
"""

from __future__ import annotations

from typing import Any

from graph.nodes import NodeDeps, finalize_node, extract_node, judge_node, persist_node, route_node, validate_node
from graph.state import GraphState


_NODES = (
    ("route", route_node),
    ("extract", extract_node),
    ("validate", validate_node),
    ("persist", persist_node),
    ("judge", judge_node),
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
    """Build and invoke the graph once, returning the final state.

    This is the convenience entry point used by the skill and the CLI. It builds
    the compiled graph, invokes it with `state`, and returns the (mutated) state.
    LangGraph returns a dict-like update; because our nodes mutate and return the
    same ``GraphState`` instance, the returned object IS the input state.
    """
    graph = build_graph(deps)
    result = graph.invoke(state)
    # langgraph may return the state object or a dict of updates; our nodes
    # return the same mutable instance, so normalize.
    if isinstance(result, GraphState):
        return result
    return state
