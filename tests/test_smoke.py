"""Trivial smoke tests so the PR check has something to run from day one.

These do NOT touch the database — they just prove the package imports and the
notebook<->Modal sentiment contract exists.
"""
import importlib


def test_package_imports():
    assert importlib.import_module("h5n1").__version__


def test_sentiment_contract_exists():
    from h5n1.sentiment import core

    for fn in ("load_model", "translate", "score_sentiment"):
        assert hasattr(core, fn), f"missing sentiment.core.{fn}"
