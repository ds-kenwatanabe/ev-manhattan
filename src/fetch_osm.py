"""Compatibility wrapper for src.graph.fetch_osm."""

from src.graph.fetch_osm import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy

    runpy.run_module("src.graph.fetch_osm", run_name="__main__")
