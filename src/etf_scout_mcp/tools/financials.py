"""Tool: get_financials — income statement, balance sheet and cash flow (OpenWebNinja)."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import openwebninja
from etf_scout_mcp.tools._google_symbol import resolve_google_symbol

Statement = Literal["income_statement", "balance_sheet", "cash_flow"]
_ALL: list[str] = ["income_statement", "balance_sheet", "cash_flow"]


class _Row(BaseModel):
    date: str | None = Field(None, description="Fiscal period end, ISO 8601")
    year: int | None = None
    quarter: int | None = Field(None, description="Fiscal quarter; only on quarterly rows")
    currency: str | None = Field(None, description="Currency of the amounts in this row")


class IncomeStatementRow(_Row):
    revenue: float | None = None
    operating_expense: float | None = None
    net_income: float | None = None
    net_profit_margin_pct: float | None = Field(None, description="Percent (27.6 = 27.6%)")
    eps: float | None = Field(None, description="Earnings per share")
    ebitda: float | None = None
    effective_tax_rate_pct: float | None = Field(None, description="Percent")


class BalanceSheetRow(_Row):
    cash_and_short_term_investments: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    shares_outstanding: float | None = None
    price_to_book: float | None = None
    return_on_assets_pct: float | None = Field(None, description="Percent")
    return_on_capital_pct: float | None = Field(None, description="Percent")


class CashFlowRow(_Row):
    net_income: float | None = None
    cash_from_operations: float | None = None
    cash_from_investing: float | None = None
    cash_from_financing: float | None = None
    net_change_in_cash: float | None = None
    free_cash_flow: float | None = None


class Financials(BaseModel):
    symbol: str = Field(description="Google Finance symbol used, e.g. 'AAPL:NASDAQ'")
    period: str = Field(description="'annual' or 'quarterly'")
    income_statement: list[IncomeStatementRow] | None = Field(None, description="Newest period first; null if not requested")
    balance_sheet: list[BalanceSheetRow] | None = Field(None, description="Newest period first; null if not requested")
    cash_flow: list[CashFlowRow] | None = Field(None, description="Newest period first; null if not requested")
    errors: dict[str, str] = Field(
        default_factory=dict,
        description="Per-statement failure message, keyed by statement name. A failed statement is null, never silently dropped.",
    )
    source: str = "openwebninja"


_MODELS: dict[str, type[_Row]] = {
    "income_statement": IncomeStatementRow,
    "balance_sheet": BalanceSheetRow,
    "cash_flow": CashFlowRow,
}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_financials(
        symbol: str | None = None,
        isin: str | None = None,
        period: Literal["annual", "quarterly"] = "annual",
        statements: list[Statement] | None = None,
        limit: int = 5,
    ) -> Financials:
        """Return a company's financial statements: income statement, balance sheet, cash flow.

        Sourced from OpenWebNinja (Google Finance); requires the server to have an
        OPENWEBNINJA_API_KEY. Each requested statement costs one upstream API call
        (cached 24h), so ask only for the statements you need.

        Money is in actual units (not millions), in the currency stated on each
        row; ratios are percent. Rows are newest first. Stocks only — ETFs have
        no statements (use get_etf_profile).

        Pair with get_stock_profile for valuation multiples and margins, or
        get_history for the price series.

        symbol: Yahoo ticker ('AAPL', 'SAP.DE') or Google-form symbol ('SAP:ETR').
                Optional when isin is provided.
        isin:   ISIN, auto-resolved to a ticker via OpenFIGI.
        period: 'annual' (default) or 'quarterly'.
        statements: any of 'income_statement', 'balance_sheet', 'cash_flow'.
                Default: all three.
        limit:  Most recent periods per statement, 1-20 (default 5).
        """
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        wanted = list(dict.fromkeys(statements or _ALL))
        google = await resolve_google_symbol(symbol, isin)
        results = await asyncio.gather(
            *[openwebninja.fetch_statement(google, s, period.upper()) for s in wanted],
            return_exceptions=True,
        )
        out = Financials(symbol=google, period=period)
        for name, res in zip(wanted, results):
            if isinstance(res, Exception):
                out.errors[name] = str(res)
                continue
            fields = _MODELS[name].model_fields
            setattr(out, name, [_MODELS[name](**{k: v for k, v in r.items() if k in fields}) for r in res[:limit]])
        return out
