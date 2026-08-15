"""Workstream-2 LangGraph parse agent.

The package imports cleanly with stdlib only. ``langgraph`` is a function-local
import inside :mod:`graph.graph` so that contract tests (which never touch the
graph builder) do not require it to be installed.
"""
