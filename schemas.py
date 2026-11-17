from pydantic import BaseModel, Field
from typing import Literal, List


class StockSignal(BaseModel):
    agent_id: Literal["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]
    symbol: str
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
    risk_tier: Literal["LOW", "MID", "HIGH"]
    action: Literal["BUY", "SKIP"]
    conviction: int = Field(ge=1, le=100)
    entry_price: float
    stop_loss: float
    target_price: float
    news_status: Literal["CLEAR", "POSITIVE_CATALYST", "NEGATIVE_RISK"]
    reasoning: str


class CommitteeOutput(BaseModel):
    selected_trades: List[FinalDecision]
