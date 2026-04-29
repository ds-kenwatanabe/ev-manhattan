"""Compatibility wrapper for src.pricing.nyiso."""

from src.pricing.nyiso import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy

    runpy.run_module("src.pricing.nyiso", run_name="__main__")
