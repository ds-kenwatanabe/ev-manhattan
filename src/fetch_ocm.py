"""Compatibility wrapper for src.data.fetch_ocm."""

from src.data.fetch_ocm import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy

    runpy.run_module("src.data.fetch_ocm", run_name="__main__")
