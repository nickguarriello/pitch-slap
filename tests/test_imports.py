"""Smoke test: every pipeline module (and main) imports cleanly.

Most production breakages here have been external-data/shape changes that
runtime health checks catch — but this guards against *code* regressions
(syntax errors, broken imports, bad top-level refs) before they ever reach a
daily run. Imports must not require ESPN creds or network (config falls back to
None creds when espn_credentials.py is absent)."""

import importlib
import pkgutil

import pipeline


def test_all_pipeline_modules_import():
    failed = []
    for m in pkgutil.iter_modules(pipeline.__path__, "pipeline."):
        try:
            importlib.import_module(m.name)
        except Exception as e:  # noqa: BLE001 — report every failure at once
            failed.append((m.name, repr(e)))
    assert not failed, f"module import failures: {failed}"


def test_main_and_config_import():
    import config  # noqa: F401
    import main    # noqa: F401
