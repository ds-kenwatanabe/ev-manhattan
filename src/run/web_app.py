"""Compatibility entry point for src.web.app."""

if __name__ == "__main__":
    from src.web.app import main

    main()
else:
    import sys
    from src.web import app as _app

    sys.modules[__name__] = _app
