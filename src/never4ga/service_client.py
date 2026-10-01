"""An interface talking to a running service.

When a service answers on loopback for *this* vault, the work goes there; when
nothing answers, the caller does it in process. Neither mode is a fallback for
a broken other one: the service is not required, and core/05 section 19's
corollary is that everything must stay useful when it is not running at all.

**It lives outside `cli/`** because MCP makes the same choice per tool call and
may not import another composition root. A leaf both roots see avoids a second
copy of the decision.

The client is deliberately thin. It carries the credential, turns a structured
error body back into a :class:`StructuredError`, and returns payloads
unchanged: the API's JSON and the CLI's JSON are the same shape by design
(details/api-cli-mcp-contract.md section 4), so a payload needs no translation
on the way through.
"""

from __future__ import annotations

import enum
from typing import Any, Final

import httpx

from never4ga.errors import Never4gaError, StructuredError

__all__ = [
    "ServiceClient",
    "ServiceNotListeningError",
    "ServicePresence",
    "ServiceRequestError",
    "ServiceTimeoutError",
    "ServiceUnavailableError",
    "presence",
    "probe",
]

#: How long a client waits before it stops waiting. It is not long enough for
#: everything the service does synchronously inside a request: a full reconcile
#: of a large vault can take minutes. No number fixes that, because the vault
#: decides the duration, and a ceiling high enough for the largest one would
#: hang a client for minutes against a service that really died. What makes a
#: timeout honest is reading the state back before reporting it, which is why
#: :class:`ServiceTimeoutError` is separate from its parent.
DEFAULT_TIMEOUT: Final = 120.0

#: The probe has to be quick: it runs before every command, and a refused
#: connection is the common case on a machine with no service.
PROBE_TIMEOUT: Final = 1.0


class ServiceUnavailableError(Never4gaError):
    """Nothing answered on the configured endpoint."""


class ServiceNotListeningError(ServiceUnavailableError):
    """Nothing accepted a connection at all.

    Distinct from its parent on purpose. "Nothing is there" and "something is
    there and has not answered yet" mean different things to a caller deciding
    whether it may write to the index. A service that is still reconciling at
    startup is listening, owns the index, and cannot say so.
    """


class ServiceTimeoutError(ServiceUnavailableError):
    """The service accepted the request and had not answered in time.

    Distinct from its parent because the work is in a different state. A
    refused connection means nothing happened; a timeout means the service is
    still doing it, or finished and the answer arrived too late. Reporting
    either as a failed command would be wrong, so a caller that can observe the
    outcome another way (`index` reads the index back) should do so first.
    """


class ServiceRequestError(Never4gaError):
    """The service answered, and the answer was a refusal."""

    def __init__(self, error: StructuredError, status_code: int) -> None:
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


class ServiceClient:
    """One vault's local service, as the CLI sees it."""

    def __init__(
        self,
        base_url: str,
        *,
        credential: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._credential = credential
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers=({"Authorization": f"Bearer {credential}"} if credential else {}),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        self._client.close()

    # -- endpoints --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def vault(self) -> dict[str, Any]:
        return self._request("GET", "/v1/vault")

    def doctor(self, level: str) -> dict[str, Any]:
        return self._request("GET", "/v1/doctor", params={"level": level})

    def reconcile(self, *, changed_only: bool) -> dict[str, Any]:
        return self._request("POST", "/v1/index/reconcile", json={"changed_only": changed_only})

    def rebuild(self) -> dict[str, Any]:
        return self._request("POST", "/v1/index/rebuild", json={})

    def concept(self, concept_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/concepts/{concept_id}")

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/concepts/search", json=request)

    def resolve_scope(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/workspaces/resolve", json=request)

    def context(self, depth: str, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/context/{depth}", json=request)

    def validate(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/concepts/validate", json=request)

    # -- internals --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.ConnectError as error:
            # Refused, or the name did not resolve: there is nothing there.
            raise ServiceNotListeningError(
                f"nothing is listening at {self._base_url}: {error}"
            ) from error
        except httpx.TimeoutException as error:
            # The request was accepted and is very likely still running: the
            # service reconciles inside the request handler and does not stop
            # because a client stopped listening.
            raise ServiceTimeoutError(
                f"the service at {self._base_url} did not answer within "
                f"{self._client.timeout.read or DEFAULT_TIMEOUT:.0f}s: {error}"
            ) from error
        except httpx.HTTPError as error:
            # A broken read, a half-open socket. Something answered the
            # connection and then did not answer the request.
            raise ServiceUnavailableError(
                f"the service at {self._base_url} did not answer: {error}"
            ) from error
        if response.is_success:
            payload: Any = response.json()
            if not isinstance(payload, dict):  # pragma: no cover - the API always sends objects
                raise ServiceUnavailableError(f"{self._base_url} answered with unexpected content")
            return payload
        raise ServiceRequestError(_structured(response), response.status_code)


def _structured(response: httpx.Response) -> StructuredError:
    """Turn a refusal back into the error the service meant to send.

    Anything that is not a section 12 body came from something that is not
    Never4gA, such as another service on the port or a proxy, and saying so is
    more useful than rendering its HTML.
    """
    try:
        body = response.json()
        error = body["error"]
        return StructuredError(
            code=str(error["code"]),
            message=str(error["message"]),
            details=dict(error.get("details") or {}),
            retryable=bool(error.get("retryable", False)),
            repair_hint=error.get("repair_hint"),
        )
    except ValueError, KeyError, TypeError:
        return StructuredError(
            "unexpected_response",
            f"the service answered {response.status_code} with a body Never4gA does not recognise",
            {"status_code": response.status_code},
            repair_hint="check that the configured port belongs to Never4gA",
        )


class ServicePresence(enum.StrEnum):
    """What is at the endpoint, as three answers rather than two.

    `probe` returns a payload or ``None``, and ``None`` means both "no
    service" and "a service that did not answer". Any caller deciding
    whether it may become a second writer has to tell those apart: the first is
    permission, the second is a service that owns the index and is too busy
    starting up to say so.
    """

    #: Nothing accepted a connection. There is no second writer to be.
    ABSENT = "absent"
    #: Something is listening and did not answer. Assume it owns the index.
    UNREACHABLE = "unreachable"
    #: It answered; the payload says which vault it holds.
    ANSWERING = "answering"


def presence(
    base_url: str,
    *,
    credential: str | None = None,
    timeout: float = PROBE_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> tuple[ServicePresence, dict[str, Any] | None]:
    """Which of the three, and the health payload when there is one."""
    client = ServiceClient(base_url, credential=credential, timeout=timeout, transport=transport)
    try:
        return ServicePresence.ANSWERING, client.health()
    except ServiceNotListeningError:
        return ServicePresence.ABSENT, None
    except Never4gaError:
        # A timeout, a 5xx, a body we could not read. Something is there.
        return ServicePresence.UNREACHABLE, None
    finally:
        client.close()


def probe(
    base_url: str,
    *,
    credential: str | None = None,
    timeout: float = PROBE_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Ask whether a Never4gA service is listening. ``None`` if none is.

    Health needs no credential (core/05 section 12 protects mutating and
    sensitive endpoints), which is what lets this run before the CLI has read
    the secret store.
    """
    client = ServiceClient(base_url, credential=credential, timeout=timeout, transport=transport)
    try:
        return client.health()
    except Never4gaError:
        return None
    finally:
        client.close()
