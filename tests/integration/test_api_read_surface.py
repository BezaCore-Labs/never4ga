"""The read-only API groups: index status, the findings ledger and work reads.

A client can read back through HTTP what it writes there, and index status
stands on its own so a client need not parse the whole vault summary.

A finding-dismissal endpoint is deliberately missing: nothing reads a dismissal
yet, and an endpoint that accepted one would let a client write a record the
product ignores.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestIndexStatus:
    def test_it_answers_without_the_whole_vault_summary(self, indexed: TestClient) -> None:
        response = indexed.get("/v1/index/status")

        assert response.status_code == 200
        assert "stale" in response.json()

    def test_it_matches_what_the_vault_summary_carries(self, indexed: TestClient) -> None:
        # The same service call behind both, so a client that reads one and a
        # client that reads the other cannot disagree about staleness.
        standalone = indexed.get("/v1/index/status").json()
        nested = indexed.get("/v1/vault").json()["index"]

        assert standalone["stale"] == nested["stale"]
        assert standalone["indexed_documents"] == nested["indexed_documents"]

    def test_reading_it_does_not_repair(self, indexed: TestClient) -> None:
        # Reads never repair, here as in the CLI. Two identical reads.
        first = indexed.get("/v1/index/status").json()
        second = indexed.get("/v1/index/status").json()

        assert first == second

    def test_it_needs_the_credential(self, anonymous: TestClient) -> None:
        assert anonymous.get("/v1/index/status").status_code == 401


class TestMaintenanceFindings:
    def test_it_answers(self, indexed: TestClient) -> None:
        response = indexed.get("/v1/maintenance/findings")

        assert response.status_code == 200
        assert isinstance(response.json()["findings"], list)

    def test_a_vault_with_no_ledger_says_so_rather_than_lying(self, indexed: TestClient) -> None:
        # `available: False` and an empty list are different claims from
        # "there are no findings", and a panel that cannot tell them apart
        # would report a healthy vault it never actually asked about.
        payload = indexed.get("/v1/maintenance/findings").json()

        assert "available" in payload

    def test_it_needs_the_credential(self, anonymous: TestClient) -> None:
        assert anonymous.get("/v1/maintenance/findings").status_code == 401


class TestWorkReads:
    """Without a configured tracker the group answers 501, and says which half.

    The read and write halves are separately available on purpose: a connection
    may be configured read-only, and a plugin that lists work should not need
    write credentials to do it. So "no reader" is its own message rather than
    the writer's.
    """

    def test_listing_says_which_half_is_missing(self, indexed: TestClient) -> None:
        response = indexed.get("/v1/work/items", params={"cwd": "/tmp"})

        assert response.status_code == 501
        assert response.json()["error"]["code"] == "work_reading_unavailable"

    def test_one_item_says_the_same(self, indexed: TestClient) -> None:
        response = indexed.get("/v1/work/items/1", params={"cwd": "/tmp"})

        assert response.status_code == 501
        assert response.json()["error"]["code"] == "work_reading_unavailable"

    def test_it_is_not_the_writers_message(self, indexed: TestClient) -> None:
        # The reader and the writer are separate factories. This fails if they
        # are merged.
        assert (
            indexed.get("/v1/work/items", params={"cwd": "/tmp"}).json()["error"]["code"]
            != "work_writing_unavailable"
        )

    def test_it_needs_the_credential(self, anonymous: TestClient) -> None:
        assert anonymous.get("/v1/work/items", params={"cwd": "/tmp"}).status_code == 401
