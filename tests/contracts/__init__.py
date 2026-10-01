"""Reusable port contract suites.

core/06 section 22: every backend interface MUST have a reusable contract test
suite, and an implementation only becomes supported once it passes the
applicable contracts. Suites here are plain mixin classes so that a future
``SQLiteTextIndex``, ``QdrantVectorIndex`` or ``Neo4jGraphIndex`` test module
subclasses them and supplies a fixture, rather than restating the behaviour.

The modules are named ``*_contract.py`` so pytest does not collect the abstract
suites directly; only the concrete ``test_*.py`` subclasses run.
"""
