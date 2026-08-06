"""Repo-root conftest.

Its only job is to exist: pytest's default `prepend` import mode inserts the
rootdir (the directory holding this file) at the front of ``sys.path``, so
tests can ``from src.sqlgate import validate_sql`` without a packaging step.
``src/`` is a PEP 420 namespace package — no ``__init__.py`` required.
"""
