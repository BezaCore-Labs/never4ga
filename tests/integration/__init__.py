"""Integration tests.

These run against temporary filesystem vaults, real SQLite, the local FastAPI
service, an in-memory MCP client and a fake OpenProject HTTP server. None of
them needs a live destructive external system in default CI.
"""
