from typing import Literal
from pydantic import BaseModel, Field


class StockSignal(BaseModel):
    agent_id: str
    symbol: str = Field(..., description="NSE Yahoo ticker ending in .NS")
    ltp: float
    entry_price: float
    stop_loss: float
    target_price: float
    rsi_14: float
    rvol: float
    beta: float
    setup_reason: str


class FinalDecision(BaseModel):
    symbol: str
    source_agent: Literal["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]
    risk_tier: Literal["LOW", "MID", "HIGH"]
    action: Literal["BUY", "SKIP"]
    conviction: int = Field(..., ge=0, le=100)
    entry_price: float
    stop_loss: float
    target_price: float
    news_status: Literal["CLEAR", "POSITIVE_CATALYST", "NEGATIVE_RISK"]
    reasoning: str


class CommitteeOutput(BaseModel):
    selected_trades: list[FinalDecision]


class IntradayEquityDecision(BaseModel):
    symbol: str = Field(..., description="NSE F&O equity ticker ending in .NS")
    source_agent: Literal["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]
    risk_tier: Literal["LOW", "MID", "HIGH"]
    action: Literal["BUY", "SKIP"]
    conviction: int = Field(..., ge=0, le=100)
    entry_price: float
    stop_loss: float
    target_price: float
    reasoning: str


class IntradayFnODecision(BaseModel):
    index_name: Literal["NIFTY", "BANKNIFTY"]
    source_agent: Literal["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]
    strategy: Literal["SINGLE_LEG_BUY", "DEBIT_SPREAD", "SKIP"]
    option_type: Literal["CE", "PE"]
    strike: int
    hedge_strike: int = Field(0, description="0 for SINGLE_LEG_BUY; OTM hedge strike for DEBIT_SPREAD")
    conviction: int = Field(..., ge=0, le=100)
    entry_premium: float
    stop_loss_premium: float
    target_premium: float
    reasoning: str


class UnifiedIntradayCommitteeOutput(BaseModel):
    market_regime: Literal["TRENDING_BULL", "TRENDING_BEAR", "RANGE_CHOP", "HIGH_VOL_EVENT"]
    equity_decisions: list[IntradayEquityDecision]
    fno_decisions: list[IntradayFnODecision]
