"""Compatibility entry point for src.graph.build_graph."""

from src.graph.build_graph import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy

    runpy.run_module("src.graph.build_graph", run_name="__main__")
