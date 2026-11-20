from pydantic import BaseModel, Field
from typing import Literal


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
