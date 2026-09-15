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
