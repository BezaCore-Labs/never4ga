"""OpenProject API v3 over httpx: authentication, paging and filters.

`details/openproject-adapter.md` section 2 chose API v3 over the provider's own
MCP endpoint, because Never4gA needs normalized context and cache behaviour
rather than one vendor's tool names.

Four behaviours of that API shape this module:

- **``offset`` is a 1-based page number, not an element offset.** A paginator
  that adds ``pageSize`` to it re-fetches overlapping windows -- and the bug is
  invisible while a collection fits on one page, which is the usual case on a
  small instance. :meth:`OpenProjectApi.collection` steps by one.
- **The default filter is ``status: open``.** A work-packages query with no
  filters silently excludes everything closed. A Never4gA query with no
  statuses means *no filter*, so :func:`work_package_filters` says ``*``
  explicitly rather than letting the server's default stand in for it.
- **A ``PATCH`` needs the ``lockVersion`` from a fresh ``GET``.** The read path
  is what supplies the version, so `normalise` keeps it.
- **A nested collection redirects.** ``/work_packages/{id}/relations`` answers
  ``308`` to ``/relations?filters=...``. A captured fixture cannot show this --
  a fixture is a body, and this is a status line. Following a redirect is not
  neutral when every request carries a credential, so
  :meth:`OpenProjectApi._send` follows one only within the same instance.

Authentication is HTTP Basic with the literal username ``apikey`` and the API
token as the password (section 4). The token reaches this module as an
argument and is never read from a file, an environment variable or the vault:
`core/03` section 16 keeps the secret half in the ``SecretStore``, and the
composition root is what joins the two.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx

from never4ga.errors import (
    ProviderUnavailableError,
    WriteConflictError,
    WriteRejectedError,
)
from never4ga.ports.work_management import WorkItemQuery

__all__ = ["API_ROOT", "OpenProjectApi", "encode_filters", "work_package_filters"]

API_ROOT: Final = "/api/v3"

#: OpenProject's own maximum is larger; this is a page size that keeps a single
#: response small enough to be cheap on a laptop-local run.
DEFAULT_PAGE_SIZE: Final = 100

#: A runaway guard, not a policy. ``total`` is authoritative and ends the loop
#: long before this, so reaching it means the server is contradicting itself.
MAX_PAGES: Final = 100

#: Long enough for a self-hosted instance over a home connection, short enough
#: that a startup pack is never held up by an unreachable tracker.
DEFAULT_TIMEOUT: Final = 10.0

#: OpenProject rewrites a nested collection into a filtered one, so one hop is
#: the shape actually seen. More than a couple means a misconfigured instance.
MAX_REDIRECTS: Final = 3


class OpenProjectApi:
    """One OpenProject instance, as HTTP.

    Every failure leaves here as :class:`ProviderUnavailableError`. That is the
    contract the port already states -- a read either answers or says it could
    not -- and it is what lets `core/05` section 19 hold: a caller degrades on
    one exception type rather than on a taxonomy of HTTP.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            auth=httpx.BasicAuth("apikey", token),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenProjectApi:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __repr__(self) -> str:
        # The token is deliberately absent: a repr reaches logs and tracebacks.
        return f"OpenProjectApi({self._base_url!r})"

    # -- requests ---------------------------------------------------------

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """The decoded body, or :class:`ProviderUnavailableError`."""
        response = self._send(path, params)
        if response.status_code >= 400:
            raise ProviderUnavailableError(self._refusal(path, response))
        return self._decode(path, response)

    def get_optional(self, path: str, params: Mapping[str, Any] | None = None) -> Any | None:
        """As :meth:`get`, but a ``404`` is absence rather than failure.

        The distinction matters to the port: ``get_work_item`` returns ``None``
        for something the tracker does not have, and raises only when the
        tracker could not be asked.
        """
        response = self._send(path, params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ProviderUnavailableError(self._refusal(path, response))
        return self._decode(path, response)

    def collection(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        limit: int | None = None,
    ) -> Sequence[Any]:
        """Every element of a collection, following pages by *page number*."""
        collected: list[Any] = []
        page = 1
        while page <= MAX_PAGES:
            payload = self.get(
                path,
                {**(params or {}), "offset": page, "pageSize": page_size},
            )
            elements = list(payload.get("_embedded", {}).get("elements", []))
            collected.extend(elements)
            if limit is not None and len(collected) >= limit:
                break
            # An empty page ends the walk whatever `total` claims: a server
            # that reports more than it will hand over must not spin here.
            if not elements:
                break
            if len(collected) >= int(payload.get("total", len(collected))):
                break
            page += 1
        return tuple(collected[:limit] if limit is not None else collected)

    # -- writes -----------------------------------------------------------

    def patch(self, path: str, body: Mapping[str, Any]) -> Any:
        """Change a resource, and hand back what the provider now holds.

        The response body is the resource, identical field for field to a
        fresh ``GET``, so this is the read-back rather than a receipt to be
        trusted on its status line.
        """
        return self._write("PATCH", path, body)

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        """Create something, or ask a form what it would accept."""
        return self._write("POST", path, body)

    def _write(self, method: str, path: str, body: Mapping[str, Any]) -> Any:
        """One write. Redirects are refused rather than followed.

        A read follows a same-origin redirect because OpenProject rewrites a
        nested collection into a filtered one. A write must not: a redirect is
        a value the server chooses, and replaying a body that changes something
        against a path we did not pick is not a risk worth the convenience. The
        method would not survive the hop intact either.
        """
        try:
            response = self._client.request(method, path, json=dict(body))
        except httpx.HTTPError as error:
            raise ProviderUnavailableError(
                f"{self._base_url} did not answer {method} {path}: {error}"
            ) from error
        if response.is_redirect:
            raise ProviderUnavailableError(
                f"{self._base_url} redirected {method} {path}; a write does not follow one"
            )
        if response.status_code == 409:
            raise WriteConflictError(
                f"{path} has moved on since it was read: {self._message(response)}"
            )
        if response.status_code in {400, 422}:
            raise WriteRejectedError(f"{self._base_url} refused {path}: {self._message(response)}")
        if response.status_code >= 400:
            raise ProviderUnavailableError(self._refusal(path, response))
        return self._decode(path, response)

    def _message(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        if not isinstance(body, dict):
            return f"HTTP {response.status_code}"
        detail = str(body.get("message", "")) or f"HTTP {response.status_code}"
        violations = body.get("_embedded", {}).get("errors", [])
        if isinstance(violations, list) and violations:
            named = ", ".join(
                str(one.get("message", "")) for one in violations if isinstance(one, dict)
            )
            if named:
                detail = f"{detail} ({named})"
        return detail

    # -- internals --------------------------------------------------------

    def _send(self, path: str, params: Mapping[str, Any] | None) -> httpx.Response:
        """One request, following a redirect only within the same instance.

        Redirects are followed here rather than by httpx, because every request
        carries the credential: httpx would re-authenticate against whatever
        host the ``Location`` named, and a redirect is a value the server
        chooses. Same origin is the boundary the token may cross.
        """
        target = path
        query: Mapping[str, Any] | None = params
        for _ in range(MAX_REDIRECTS + 1):
            try:
                # `params={}` is not "no parameters" to httpx: an empty mapping
                # *replaces* the query a URL already carries, which would strip
                # the filters off a redirect target and quietly widen the read.
                response = self._client.get(target, params=dict(query) if query else None)
            except httpx.HTTPError as error:
                # `str(error)` on an httpx error carries the URL but never the
                # Authorization header, so nothing secret travels with it.
                raise ProviderUnavailableError(
                    f"{self._base_url} did not answer {path}: {error}"
                ) from error
            location = response.headers.get("location")
            if not response.is_redirect or not location:
                return response
            redirected = response.url.join(location)
            if (redirected.scheme, redirected.host, redirected.port) != (
                response.url.scheme,
                response.url.host,
                response.url.port,
            ):
                raise ProviderUnavailableError(
                    f"{self._base_url} redirected {path} to "
                    f"{redirected.scheme}://{redirected.netloc.decode()}, "
                    "which is a different instance; the credential does not follow"
                )
            # The Location carries its own query -- OpenProject rewrites a
            # nested collection into a filtered one -- so the original
            # parameters must not be sent again.
            target = str(redirected)
            query = None
        raise ProviderUnavailableError(
            f"{self._base_url} kept redirecting {path}; gave up after {MAX_REDIRECTS}"
        )

    def _decode(self, path: str, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as error:
            raise ProviderUnavailableError(
                f"{self._base_url} answered {path} with something that is not JSON"
            ) from error

    def _refusal(self, path: str, response: httpx.Response) -> str:
        detail = ""
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            detail = str(body.get("message", ""))
        if response.status_code in {401, 403}:
            # Say that the credential was refused without saying anything about
            # the credential.
            detail = detail or "the stored credential was refused"
        return f"{self._base_url} refused {path} with {response.status_code}" + (
            f": {detail}" if detail else ""
        )


def work_package_filters(
    query: WorkItemQuery,
    *,
    status_ids: Sequence[str],
    own_project_only: bool = False,
) -> list[dict[str, Any]]:
    """The OpenProject filter array for a provider-neutral query.

    ``status_ids`` is already resolved: the API filters on ids, a Never4gA query
    names things the way a person does, and resolving a name to an id is the
    provider's job rather than the caller's.

    ``assignees`` is deliberately absent. The API filters on principal ids,
    not names, so the provider filters assignees by name itself.

    ``own_project_only`` narrows a project's listing to that project. The
    instance otherwise includes every subproject's work packages, so a parent
    workspace would list its children's work as its own. `subprojectId !*`
    means nothing without a project, so it is only sent with one.
    """
    filters: list[dict[str, Any]] = []
    if query.statuses:
        filters.append({"status": {"operator": "=", "values": list(status_ids)}})
    else:
        # Never let the server's `status: open` default answer a question
        # nobody asked. No statuses means every status.
        filters.append({"status": {"operator": "*", "values": []}})
    if query.terms:
        filters.append({"search": {"operator": "**", "values": list(query.terms)}})
    if own_project_only:
        filters.append({"subprojectId": {"operator": "!*", "values": []}})
    if query.updated_since is not None:
        # `<>d` is a date range with an open upper bound.
        filters.append(
            {
                "updatedAt": {
                    "operator": "<>d",
                    "values": [query.updated_since.isoformat(), ""],
                }
            }
        )
    return filters


def encode_filters(filters: Sequence[Mapping[str, Any]]) -> str:
    """Filters travel as one JSON-encoded query parameter."""
    return json.dumps(list(filters), separators=(",", ":"))
