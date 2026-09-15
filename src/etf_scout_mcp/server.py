"""FastMCP instance, transport switch, and tool registration."""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, AuthProvider

from etf_scout_mcp.config import config
from etf_scout_mcp.tools import (
    batch_quote,
    etf_compare,
    etf_listings,
    etf_profile,
    history,
    portfolio_xray,
    quote,
    search,
)


class _StaticBearerAuth(AuthProvider):
    """Accepts exactly one pre-shared bearer token; rejects everything else."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == self._token:
            return AccessToken(token=token, client_id="homelab", scopes=[])
        return None


def _make_auth() -> AuthProvider | None:
    """Auth provider for HTTP transport.

    Precedence: OIDC (remote OAuth via an upstream IdP such as PocketID, required by
    Claude's remote connectors) > static bearer token (machine-to-machine). stdio needs
    no auth.
    """
    if config.transport != "http":
        return None

    if config.oidc_enabled:
        from fastmcp.server.auth.oidc_proxy import OIDCProxy

        return OIDCProxy(
            config_url=config.oidc_config_url,
            client_id=config.oidc_client_id,
            client_secret=config.oidc_client_secret,
            base_url=config.oidc_base_url,
            redirect_path=config.oidc_redirect_path,
            required_scopes=config.oidc_required_scopes,
            allowed_client_redirect_uris=config.oidc_allowed_redirect_uris,
            verify_id_token=config.oidc_verify_id_token,
            forward_resource=config.oidc_forward_resource,
        )

    if not config.http_bearer_token:
        raise RuntimeError(
            "HTTP transport needs auth: set OIDC_* (config_url/client_id/client_secret/base_url) "
            "or ETF_SCOUT_MCP_HTTP_BEARER_TOKEN"
        )
    return _StaticBearerAuth(config.http_bearer_token)


def _make_mcp() -> FastMCP:
    auth = _make_auth()

    return FastMCP(
        name="etf-scout-mcp",
        instructions="ETF research tools sourced from justETF, Yahoo Finance, and OpenFIGI.",
        auth=auth,
    )


mcp = _make_mcp()

# Register all tools at import time so `fastmcp inspect/dev` sees them
# without having to call main().
etf_profile.register(mcp)
quote.register(mcp)
batch_quote.register(mcp)
history.register(mcp)
etf_compare.register(mcp)
search.register(mcp)
etf_listings.register(mcp)
portfolio_xray.register(mcp)


def main() -> None:
    if config.transport == "http":
        mcp.run(
            transport="streamable-http",
            host=config.http_host,
            port=config.http_port,
        )
    else:
        mcp.run(transport="stdio")
