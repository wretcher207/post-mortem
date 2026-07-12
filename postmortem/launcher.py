"""Unified entry point for the packaged Post Mortem sidecar bundle."""

import runpy
import sys

from . import __version__


def _run_reaperd(argv):
    if not argv:
        print("postmortem-sidecar reaperd requires the path to reaperd.py", file=sys.stderr)
        return 2
    script, *script_args = argv
    previous = sys.argv
    try:
        sys.argv = [script, *script_args]
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as error:
            return error.code if isinstance(error.code, int) else 1
    finally:
        sys.argv = previous
    return 0


def _run_bundle_tests(argv):
    import pytest

    return pytest.main(argv or ["-q", "tests"])


def _run_code(source):
    namespace = {"__name__": "__main__", "__builtins__": __builtins__}
    exec(compile(source, "<string>", "exec"), namespace, namespace)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--version"]:
        print(f"postmortem-sidecar {__version__}")
        return 0
    if argv and argv[0] == "cli":
        from . import cli

        return cli.main(argv[1:])
    if argv and argv[0] == "reaperd":
        return _run_reaperd(argv[1:])
    if argv and argv[0] == "test-bundle":
        return _run_bundle_tests(argv[1:])
    if len(argv) == 2 and argv[0] == "-c":
        return _run_code(argv[1])
    if argv and argv[0] == "service":
        argv = argv[1:]
    from . import service

    return service.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
