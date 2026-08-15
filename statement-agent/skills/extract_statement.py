"""Extract one PDF through the workstream-2 parse graph.

This is the skill entry point: build the graph with injected ports and run one
PDF through it. The caller supplies the :class:`NodeDeps` (the injected ABC
implementations); this module never constructs concrete stores/judge/trace
itself -- that would couple the skill to WS3/WS4/WS5, which are built in
parallel. At deploy time ``app/main.py`` (WS6) wires the real ports; in tests the
in-memory fakes from :mod:`graph.fakes` are used.
"""

from __future__ import annotations

from contracts.models import ParseRequest
from graph.nodes import NodeDeps
from graph.state import GraphState


def extract_statement(request: ParseRequest, deps: NodeDeps) -> GraphState:
    """Run `request` through the parse graph and return the final state.

    This is a thin wrapper over :func:`graph.graph.run_graph` that constructs the
    initial :class:`GraphState`. Langgraph is imported lazily inside
    ``run_graph``, so importing this skill does not require langgraph to be
    installed -- only calling it does.
    """
    from graph.graph import run_graph  # function-local; langgraph imported there

    state = GraphState(request=request)
    return run_graph(deps, state)
