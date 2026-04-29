"""Compatibility entry point for src.data.build_instance."""

from src.data.build_instance import *  # noqa: F401,F403


if __name__ == "__main__":
    import runpy

    runpy.run_module("src.data.build_instance", run_name="__main__")
