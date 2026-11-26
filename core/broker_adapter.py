import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


@dataclass
class BrokerOrderResult:
    success: bool
    broker: str
    order_id: str
    gtt_id: str
    symbol: str
    qty: int
    filled_price: float
    status: str
    message: str


class BaseNSEBrokerAdapter:
    def __init__(self, live_mode: bool = False, allocated_capital: float = 100000.0, max_daily_loss_inr: float = 5000.0):
        self.live_mode = live_mode and (os.getenv("LIVE_MODE", "false").strip().lower() == "true")
        self.allocated_capital = allocated_capital
        self.max_daily_loss_inr = max_daily_loss_inr
        self.kill_switch_triggered = False

    def check_kill_switch(self, realized_today_pnl: float, unrealized_pnl: float) -> tuple[bool, str]:
        net_day = realized_today_pnl + unrealized_pnl
        if self.kill_switch_triggered or net_day <= -abs(self.max_daily_loss_inr):
            self.kill_switch_triggered = True
            return True, f"KILL SWITCH ACTIVE: Daily P&L ₹{net_day:+,.2f} <= -₹{self.max_daily_loss_inr:,.2f}"
        return False, "OK"

    def place_delivery_buy_with_gtt(self, symbol: str, qty: int, limit_price: float, stop_loss: float, target_price: float) -> BrokerOrderResult:
        return BrokerOrderResult(
            success=True,
            broker="PAPER_SIM",
            order_id=f"PAPER-{int(datetime.now(timezone.utc).timestamp())}",
            gtt_id="PAPER-GTT",
            symbol=symbol,
            qty=qty,
            filled_price=limit_price,
            status="PAPER_FILLED",
            message="Paper order simulated (LIVE_MODE=false).",
        )


class DhanHQAdapter(BaseNSEBrokerAdapter):
    def __init__(self, live_mode: bool = False, allocated_capital: float = 100000.0, max_daily_loss_inr: float = 5000.0):
        super().__init__(live_mode, allocated_capital, max_daily_loss_inr)
        self.client_id = os.getenv("DHAN_CLIENT_ID", "")
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN", "")


class AngelOneSmartAPIAdapter(BaseNSEBrokerAdapter):
    def __init__(self, live_mode: bool = False, allocated_capital: float = 100000.0, max_daily_loss_inr: float = 5000.0):
        super().__init__(live_mode, allocated_capital, max_daily_loss_inr)
        self.api_key = os.getenv("ANGEL_API_KEY", "")


def get_broker_adapter(broker_name: str = "PAPER", live_mode: bool = False, allocated_capital: float = 100000.0) -> BaseNSEBrokerAdapter:
    b = str(broker_name or "PAPER").strip().upper()
    if b == "DHAN":
        return DhanHQAdapter(live_mode=live_mode, allocated_capital=allocated_capital)
    if b == "ANGEL":
        return AngelOneSmartAPIAdapter(live_mode=live_mode, allocated_capital=allocated_capital)
    return BaseNSEBrokerAdapter(live_mode=False, allocated_capital=allocated_capital)


class BrokerAdapter:
    """Safe paper-first execution adapter. Defaults strictly to LIVE_MODE=false."""

    def __init__(self) -> None:
        self.live_mode = os.getenv("LIVE_MODE", "false").strip().lower() == "true"

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        ist_now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
        if not self.live_mode:
            return {
                "accepted": True,
                "mode": "PAPER",
                "order_id": f"PAPER-{int(datetime.now(timezone.utc).timestamp())}",
                "timestamp": ist_now,
                "order": order,
            }
        return {
            "accepted": False,
            "mode": "LIVE_BLOCKED_NO_BROKER_KEY",
            "timestamp": ist_now,
            "reason": "Broker API credentials not configured; order rejected safely.",
        }
