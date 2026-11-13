"""Pydantic response models for all tool outputs."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Holding(BaseModel):
    name: str
    isin: str | None = None
    weight: float | None = Field(None, description="Portfolio weight in percent (5.3 = 5.3%)")


class Allocation(BaseModel):
    name: str
    weight: float | None = Field(None, description="Allocation weight in percent (67.3 = 67.3%)")


class EtfProfile(BaseModel):
    isin: str
    name: str | None = None
    description: str | None = None
    index: str | None = None
    investment_focus: str | None = None

    fund_size_eur: float | None = Field(
        None, description="Total fund assets in EUR (not millions — actual EUR value)"
    )
    ter: float | None = Field(
        None,
        description=(
            "Total Expense Ratio as a decimal, NOT a percentage. "
            "0.002 means 0.20% (20 bps). 0.0007 means 0.07% (7 bps)."
        ),
    )
    replication: str | None = None
    distribution_policy: str | None = Field(None, description="'Accumulating' or 'Distributing'")
    distribution_frequency: str | None = None
    fund_currency: str | None = None
    currency_hedged: bool | None = None
    fund_domicile: str | None = None
    fund_provider: str | None = None
    legal_structure: str | None = None
    sustainability: bool | None = None
    volatility_1y: float | None = Field(None, description="1-year volatility in percent (10.6 = 10.6%)")
    inception_date: str | None = Field(None, description="ISO 8601 date string, e.g. '2009-09-25'")
    holdings_date: str | None = Field(None, description="ISO 8601 date of the holdings snapshot")
    data_as_of: str | None = Field(None, description="ISO 8601 date this profile was scraped from justETF")

    return_1y: float | None = Field(
        None, description="1-year cumulative total return in percent (24.76 = +24.76%), as reported by justETF"
    )
    return_3y: float | None = Field(None, description="3-year CUMULATIVE (not annualised) total return in percent")
    return_5y: float | None = Field(None, description="5-year CUMULATIVE (not annualised) total return in percent")
    return_3y_annualised_pct: float | None = Field(
        None, description="3-year return annualised (CAGR) from return_3y"
    )
    return_5y_annualised_pct: float | None = Field(
        None, description="5-year return annualised (CAGR) from return_5y"
    )

    top_holdings: list[Holding] = Field(default_factory=list)
    countries: list[Allocation] = Field(default_factory=list)
    sectors: list[Allocation] = Field(default_factory=list)

    error: str | None = Field(
        None, description="Set when isin could not be resolved on justETF — all other fields are null"
    )


class EtfSummary(BaseModel):
    """Lightweight ETF row returned by compare and search tools."""

    isin: str
    name: str | None = None
    ticker: str | None = None
    fund_provider: str | None = None
    fund_domicile: str | None = None
    fund_currency: str | None = Field(None, description="Fund base/reporting currency, e.g. 'EUR', 'USD'")
    fund_size_eur: float | None = Field(
        None, description="Total fund assets in EUR (actual EUR value, not millions)"
    )
    ter: float | None = Field(
        None,
        description=(
            "Total Expense Ratio as a decimal, NOT a percentage. "
            "0.002 means 0.20% (20 bps)."
        ),
    )
    replication: str | None = None
    distribution_policy: str | None = Field(None, description="'Accumulating' or 'Distributing'")
    currency_hedged: bool | None = None
    sustainability: bool | None = None
    inception_date: str | None = Field(None, description="ISO 8601 date string")
    return_1y: float | None = Field(
        None, description="1-year cumulative total return in percent (24.76 = +24.76%), as reported by justETF"
    )
    return_3y: float | None = Field(None, description="3-year CUMULATIVE (not annualised) total return in percent")
    return_5y: float | None = Field(None, description="5-year CUMULATIVE (not annualised) total return in percent")
    return_3y_annualised_pct: float | None = Field(
        None, description="3-year return annualised (CAGR) from return_3y"
    )
    return_5y_annualised_pct: float | None = Field(
        None, description="5-year return annualised (CAGR) from return_5y"
    )
    volatility_1y: float | None = Field(None, description="1-year volatility in percent")
    leverage_factor: float | None = Field(
        None,
        description="Best-effort leverage multiple (e.g. 3.0 for a 3x fund) detected from the "
        "fund name. Not sourced from justETF — a regex heuristic. Null whenever undetected; "
        "never fabricated as 1.0 for an unleveraged fund.",
    )
    data_as_of: str | None = Field(None, description="ISO 8601 date this row was scraped from justETF")
    error: str | None = Field(
        None, description="Set when this ISIN could not be resolved — all other fields are null"
    )


class StockSummary(BaseModel):
    """Lightweight single-stock row returned by get_stock_profile / compare_stocks.

    Conventions differ from ETF models where the source dictates it: money fields
    are in the stock's own trading/reporting currency (not converted to EUR),
    ratios such as margins, growth and yield are PERCENT (27.6 = 27.6%).
    """

    symbol: str = Field(description="Yahoo Finance ticker, e.g. 'AAPL' or 'SAP.DE'")
    isin: str | None = Field(None, description="Echoed from the request when an ISIN was given; not looked up")
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    exchange: str | None = None
    currency: str | None = Field(None, description="Trading currency of this listing, e.g. 'USD', 'EUR'")
    financial_currency: str | None = Field(None, description="Currency of the financial statements (can differ from trading currency)")
    market_cap: float | None = Field(None, description="Market capitalisation, actual value (not millions), in `currency`")

    pe_trailing: float | None = None
    pe_forward: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    eps_trailing: float | None = None

    dividend_yield_pct: float | None = Field(None, description="Dividend yield in percent (0.3 = 0.3%)")
    dividend_rate: float | None = Field(None, description="Annual dividend per share, in `currency`")
    payout_ratio_pct: float | None = Field(None, description="Dividend payout ratio in percent")

    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    profit_margin_pct: float | None = None
    roe_pct: float | None = Field(None, description="Return on equity in percent")
    roa_pct: float | None = Field(None, description="Return on assets in percent")
    revenue_growth_pct: float | None = Field(None, description="Year-over-year revenue growth in percent (most recent quarter)")
    earnings_growth_pct: float | None = Field(None, description="Year-over-year earnings growth in percent (most recent quarter)")

    debt_to_equity: float | None = Field(None, description="Total debt / equity as a ratio (0.84 = 84%)")
    current_ratio: float | None = None
    beta: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None

    source: str | None = Field(None, description="'yahoo' or 'eodhd'. Null when the lookup failed.")
    data_as_of: str | None = Field(None, description="ISO 8601 date this data was fetched")
    error: str | None = Field(
        None, description="Set when the stock could not be resolved — all data fields are null"
    )


class StockProfile(StockSummary):
    """Full single-stock profile: StockSummary plus description and size/analyst fields."""

    description: str | None = None
    website: str | None = None
    employees: int | None = None
    revenue: float | None = Field(None, description="Trailing-12-month revenue in `financial_currency`")
    ebitda: float | None = Field(None, description="Trailing-12-month EBITDA in `financial_currency`")
    free_cash_flow: float | None = Field(None, description="Trailing-12-month free cash flow in `financial_currency`")
    shares_outstanding: float | None = None
    analyst_target_mean: float | None = Field(None, description="Mean analyst price target, in `currency`")
    analyst_recommendation: str | None = Field(None, description="Yahoo consensus key, e.g. 'buy', 'hold'")
    analyst_count: int | None = None


class StockSearchResult(BaseModel):
    symbol: str = Field(description="Yahoo Finance ticker — pass to get_stock_profile / get_quote")
    name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
