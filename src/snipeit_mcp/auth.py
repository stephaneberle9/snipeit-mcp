"""FastMCP OAuth provider wired to Snipe-IT's Laravel Passport.

Snipe-IT exposes the standard Passport endpoints under ``/oauth/authorize`` and
``/oauth/token`` (managed at ``/admin/oauth`` in the Snipe-IT UI). Passport
issues opaque access tokens, not JWTs, so :class:`SnipeITTokenVerifier` validates
each token by calling ``/api/v1/users/me`` — a 200 means the token is live and
belongs to the user described in the response body.
"""

from __future__ import annotations

import logging
import time

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy

logger = logging.getLogger(__name__)


def passport_endpoints(snipeit_url: str) -> tuple[str, str]:
    """Return the ``(authorize, token)`` Passport endpoint URLs for a Snipe-IT base URL.

    Strips a trailing slash from ``snipeit_url`` and appends Snipe-IT's standard
    Laravel Passport paths. Extracted as a pure function so the URL-building logic
    can be tested without reaching into FastMCP's private ``OAuthProxy`` internals.
    """
    base = snipeit_url.rstrip("/")
    return f"{base}/oauth/authorize", f"{base}/oauth/token"


class SnipeITTokenVerifier(TokenVerifier):
    """Validate Snipe-IT Passport tokens via ``GET /api/v1/users/me``.

    Passport tokens are opaque, so we can't decode them locally — we ask the
    upstream Snipe-IT instance whether the token is still good. A 200 response
    yields a populated :class:`AccessToken`; a 401 (or any other failure) yields
    ``None`` and the request is rejected by FastMCP with the usual 401.
    """

    def __init__(
        self,
        *,
        snipeit_url: str,
        timeout_seconds: int = 10,
        cache_ttl_seconds: int = 60,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(required_scopes=required_scopes)
        self.snipeit_url = snipeit_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Short-lived positive-result cache so we don't hit /users/me on every
        # single tool call. Keyed on the opaque token; only successful (200)
        # validations are cached. Set ``cache_ttl_seconds=0`` to disable.
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, AccessToken]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        cached = self._cache_get(token)
        if cached is not None:
            return cached

        url = f"{self.snipeit_url}/api/v1/users/me"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as http:
                response = await http.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            # Log only the exception class name, never str(exc): httpx error
            # strings can echo back the request URL, and a future change here
            # could accidentally surface bearer headers in logs.
            exc_name = type(exc).__name__
            logger.warning("Snipe-IT /users/me request raised %s", exc_name)
            return None

        if response.status_code != 200:
            status = response.status_code
            logger.debug("Snipe-IT /users/me returned HTTP %d", status)
            return None

        try:
            user = response.json()
        except ValueError:
            logger.warning("Snipe-IT /users/me returned a non-JSON 200 response")
            return None

        # Passport doesn't expose scopes through /users/me — return an empty list.
        access = AccessToken(
            token=token,
            client_id=str(user.get("id", "snipeit-user")),
            scopes=[],
            expires_at=None,
        )
        self._cache_put(token, access)
        return access

    def _cache_get(self, token: str) -> AccessToken | None:
        """Return a non-expired cached :class:`AccessToken` for ``token``, or ``None``."""
        if self.cache_ttl_seconds <= 0:
            return None
        entry = self._cache.get(token)
        if entry is None:
            return None
        expires_at, access = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(token, None)
            return None
        return access

    def _cache_put(self, token: str, access: AccessToken) -> None:
        """Cache a successful validation for ``cache_ttl_seconds``."""
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[token] = (time.monotonic() + self.cache_ttl_seconds, access)


class SnipeITOAuthProvider(OAuthProxy):
    """OAuth proxy that delegates to Snipe-IT's Laravel Passport.

    The MCP client (e.g. Claude.ai) sees a regular OAuth 2.0 + PKCE flow against
    *this* server. Authorization requests are forwarded upstream to Snipe-IT,
    where the user logs in (typically bouncing through Google SAML SSO on the
    way) and authorises the Snipe-IT OAuth client. Snipe-IT issues an opaque
    Passport access token which is then handed back to the MCP client and used
    on every tool call.
    """

    def __init__(
        self,
        *,
        snipeit_url: str,
        client_id: str,
        client_secret: str,
        base_url: str,
        redirect_path: str = "/auth/callback",
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        cache_ttl_seconds: int = 60,
    ):
        authorize_url, token_url = passport_endpoints(snipeit_url)
        super().__init__(
            upstream_authorization_endpoint=authorize_url,
            upstream_token_endpoint=token_url,
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=SnipeITTokenVerifier(
                snipeit_url=snipeit_url,
                timeout_seconds=timeout_seconds,
                cache_ttl_seconds=cache_ttl_seconds,
                required_scopes=required_scopes,
            ),
            base_url=base_url,
            redirect_path=redirect_path,
        )
