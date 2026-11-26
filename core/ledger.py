import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

try:
    import streamlit as st
except Exception:
    st = None

from core.charges import estimate_roundtrip_charges
from core.config import MASTER_TRADES_HEADERS, get_desk_config, normalize_desk_key
from core.market_data import get_live_quotes_batch

LOCAL_LEDGER_PATH = Path("data/v48_ledger.json")
MASTER_SHEET_TITLE = "Ledger"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_secret(key: str) -> Optional[Any]:
    aliases = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": ["GOOGLE_SERVICE_ACCOUNT_JSON", "GCP_SERVICE_ACCOUNT_JSON"],
        "GOOGLE_SHEET_ID": ["GOOGLE_SHEET_ID", "MASTER_SHEET_ID", "SWING_SHEET_ID", "UNIFIED_2L_SHEET_ID", "SHEET_ID"],
    }.get(key, [key])

    for alias in aliases:
        val = os.getenv(alias)
        if val:
            return val
        if st is not None:
            try:
                if alias in st.secrets:
                    return st.secrets[alias]
            except Exception:
                pass

    if st is not None and key == "GOOGLE_SERVICE_ACCOUNT_JSON":
        try:
            if "gcp_service_account" in st.secrets:
                return dict(st.secrets["gcp_service_account"])
        except Exception:
            pass
    return None


def _connect_master_sheet() -> tuple[Optional[Any], str]:
    if gspread is None or Credentials is None:
        return None, "Single Ledger (Local JSON fallback: gspread/google-auth not installed)"
    sa_raw = _get_secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_raw:
        return None, "Single Ledger (Local JSON fallback: service account secret not set)"
    try:
        info = json.loads(sa_raw) if isinstance(sa_raw, str) else dict(sa_raw)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet_id = _get_secret("GOOGLE_SHEET_ID")
        sh = None
        if sheet_id and str(sheet_id).strip():          # explicit ID always wins
            sh = client.open_by_key(str(sheet_id).strip())
        else:
            for title_try in ("Ledger", "NSE_5Agent_Ledger", "NSE_Unified_2L_Ledger"):
                try:
                    sh = client.open(title_try)
                    break
                except Exception:
                    sh = None
        if sh is None:
            return None, "Single Ledger (Local fallback: no sheet found - set GOOGLE_SHEET_ID)"
        return sh, f"Single Ledger Synced: {sh.title}"
    except Exception as exc:
        return None, f"Single Ledger (Local fallback: {exc})"


def sheets_error_hint(err: str) -> str:
    """Translate the usual Google Sheets write failures into what the user must actually do."""
    e = (err or "").lower()
    if "not supported for this document" in e or "failed_precondition" in e:
        return (f"{err}  ->  The file is still an Excel (.xlsx) file. In Google Sheets use File > Save as Google Sheets, "
                "then use the NEW sheet's ID.")
    if "403" in e or "permission" in e or "the caller does not have" in e:
        return f"{err}  ->  Share the sheet with the service-account email as EDITOR (not Viewer)."
    if "429" in e or "quota" in e:
        return f"{err}  ->  Google Sheets API rate limit; the runner now batches writes - wait a minute and retry."
    if "404" in e or "not found" in e:
        return f"{err}  ->  Wrong sheet ID, or the tab name (trades/account/agent_messages) is missing."
    return err or "unknown error"


def _a1(row: int, col: int) -> str:
    """(row, col) -> A1 notation without needing gspread.utils."""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def ensure_master_headers(trades_ws: Any) -> List[str]:
    existing = trades_ws.row_values(1)
    if not existing:
        trades_ws.update(range_name="A1", values=[MASTER_TRADES_HEADERS])
        return list(MASTER_TRADES_HEADERS)
    norm_existing = {h.strip().lower() for h in existing if str(h).strip()}
    missing = [h for h in MASTER_TRADES_HEADERS if h.lower() not in norm_existing]
    if missing:
        merged = existing + missing
        if len(merged) > trades_ws.col_count:
            trades_ws.add_cols(len(merged) - trades_ws.col_count)
        trades_ws.update(range_name="A1", values=[merged])
        return merged
    return existing


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or str(val).strip() == "":
            return default
        return float(val)
    except Exception:
        return default


def _canonical_desk(desk_val: Any, symbol_val: Any = "", book_val: Any = "") -> str:
    raw = str(desk_val or "").strip().lower().replace("&", "").replace(" ", "")
    bk = str(book_val or "").strip().upper()
    sym = str(symbol_val or "").strip().upper()
    if raw in ("fno", "fo") or bk == "FNO" or (
        not raw and ("NIFTY " in sym or "BANKNIFTY " in sym) and (sym.endswith(" CE") or sym.endswith(" PE"))
    ):
        return "F&O"
    if raw == "intraday" or bk == "EQUITY":
        return "Intraday"
    if raw == "commodity" or bk == "COMMODITY" or sym.endswith(" MCX"):
        return "Commodity"
    return "Swing"


import threading

LEDGER_WRITE_LOCK = threading.RLock()      # watchdog thread and cycle thread must not interleave ledger writes
_STRUCTURE_CHECKED: set = set()
ACCOUNT_HEADERS = ["swing_cash", "swing_peak_pnl", "eq_cash", "fno_cash", "comm_cash", "combined_peak_pnl", "locked_floor", "peak_date", "desk_settings"]
ACCOUNT_DEFAULTS = [1000000.0, 0.0, 100000.0, 100000.0, 100000.0, 0.0, 0.0, "", ""]
MESSAGE_HEADERS = ["cycle_id", "timestamp", "sender", "recipient", "msg_type", "summary", "payload_json"]


def ensure_sheet_structure(sh: Any) -> str:
    """First-connection setup: create missing tabs, write header rows, seed the account row. Never overwrites data."""
    notes = []
    for name, cols in (("account", 12), ("agent_messages", 7), ("trades", 32)):
        try:
            sh.worksheet(name)
        except Exception:
            sh.add_worksheet(title=name, rows=1000, cols=cols)
            notes.append(f"created {name}")
    acc = sh.worksheet("account")
    headers = [h for h in acc.row_values(1)]
    if not any(str(h).strip() for h in headers):
        acc.update(range_name="A1", values=[ACCOUNT_HEADERS, ACCOUNT_DEFAULTS])
        notes.append("seeded account")
    else:
        missing = [h for h in ACCOUNT_HEADERS if h not in headers]
        for h in missing:
            headers.append(h)
            acc.update_cell(1, len(headers), h)
        if missing:
            notes.append(f"account +{len(missing)} columns")
        if not any(str(v).strip() for v in acc.row_values(2)):
            row2 = [ACCOUNT_DEFAULTS[ACCOUNT_HEADERS.index(h)] if h in ACCOUNT_HEADERS else "" for h in headers]
            acc.update(range_name="A2", values=[row2])
            notes.append("seeded account row 2")
    msgs = sh.worksheet("agent_messages")
    if not any(str(h).strip() for h in msgs.row_values(1)):
        msgs.update(range_name="A1", values=[MESSAGE_HEADERS])
        notes.append("agent_messages headers")
    ensure_master_headers(sh.worksheet("trades"))
    return ", ".join(notes)


def _cash_blocked(desk: Any, entry: float, qty: int) -> float:  # desk may be 'F&O'
    """Cash tied up by a position: full premium/price for cash products, margin for futures."""
    try:
        margin = float(get_desk_config(str(desk)).margin_pct)
    except Exception:
        margin = 1.0
    return float(entry) * int(qty) * margin


def _normalize_master_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    sym = str(out.get("symbol", "")).strip().upper()
    desk = _canonical_desk(out.get("desk"), sym, out.get("book"))
    book = str(out.get("book", "")).strip().upper() or (
        "FNO" if desk == "F&O" else ("COMMODITY" if desk == "Commodity" else ("EQUITY" if desk == "Intraday" else "SWING"))
    )
    tier = str(out.get("tier") or out.get("strategy") or ("HIGH" if desk == "F&O" else "MID")).strip().upper()
    status = str(out.get("status", "OPEN")).strip().upper() or "OPEN"
    side = str(out.get("side", "LONG")).strip().upper() or "LONG"

    lot_size = max(1, int(_safe_float(out.get("lot_size", 1), 1.0)))
    raw_qty = int(_safe_float(out.get("qty", 0), 0.0))
    raw_qol = int(_safe_float(out.get("qty_or_lots", 0), 0.0))
    qty = raw_qty if raw_qty > 0 else (raw_qol * lot_size if raw_qol > 0 else 0)
    qty_or_lots = raw_qol if raw_qol > 0 else qty

    entry = _safe_float(out.get("entry", 0.0))
    sl = _safe_float(out.get("sl", 0.0))
    target = _safe_float(out.get("target", 0.0))
    initial_target = _safe_float(out.get("initial_target", 0.0)) or target
    exit_price = _safe_float(out.get("exit_price", 0.0))
    raw_pnl = out.get("pnl") if str(out.get("pnl", "")).strip() != "" else out.get("net_pnl", 0.0)
    pnl = _safe_float(raw_pnl, 0.0)

    raw_init_sl = _safe_float(out.get("initial_sl", 0.0))
    initial_sl = raw_init_sl if raw_init_sl > 0 else (sl if sl > 0 else round(entry * 0.98, 2))
    gross_pnl = _safe_float(out.get("gross_pnl", pnl), pnl)
    charges = _safe_float(out.get("charges_slippage", 0.0), 0.0)

    risk_unit = max(0.05, abs(entry - initial_sl))
    realized_r = _safe_float(out.get("realized_r", 0.0))
    if realized_r == 0.0 and status != "OPEN" and exit_price > 0 and entry > 0:
        direction = -1.0 if side == "SHORT" else 1.0
        realized_r = round(((exit_price - entry) * direction) / risk_unit, 2)

    ver = str(out.get("version", "")).strip() or "v4.8"
    tag = str(out.get("allocation_tag", "")).strip() or ("LEGACY_V472" if ver == "v4.7.2" else "STANDARD_ALLOC")
    reason_val = str(out.get("reason") or out.get("reasoning") or "").strip()

    return {
        "ledger_book": MASTER_SHEET_TITLE,
        "timestamp": str(out.get("timestamp", "")).strip(),
        "exit_timestamp": str(out.get("exit_timestamp", "")).strip(),
        "desk": desk,
        "book": book,
        "symbol": sym,
        "sector": str(out.get("sector", "INDEX" if desk == "F&O" else "GENERAL")).strip().upper() or "GENERAL",
        "tier": tier,
        "strategy": str(out.get("strategy", tier)).strip().upper(),
        "side": side,
        "status": status,
        "qty": qty,
        "qty_or_lots": qty_or_lots,
        "lot_size": lot_size,
        "entry": entry,
        "initial_sl": initial_sl,
        "sl": sl,
        "target": target,
        "initial_target": initial_target,
        "exit_price": exit_price,
        "gross_pnl": gross_pnl,
        "charges_slippage": charges,
        "pnl": pnl,
        "net_pnl": pnl,
        "realized_r": realized_r,
        "mfe_pct": _safe_float(out.get("mfe_pct", 0.0)),
        "mae_pct": _safe_float(out.get("mae_pct", 0.0)),
        "version": ver,
        "allocation_tag": tag,
        "reason": reason_val,
    }


def enrich_watch_metrics(trade: Dict[str, Any], quotes_map: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    t = dict(trade)
    sym = str(t["symbol"]).strip().upper()
    entry = float(t["entry"])
    sl = float(t["sl"])
    target = float(t["target"])
    qty = int(t["qty"])
    side = t.get("side", "LONG")
    desk = t.get("desk", "Swing")

    q = (quotes_map or {}).get(sym) or {"ltp": entry}
    ltp = float(q.get("ltp", entry)) if t["status"] == "OPEN" else (float(t["exit_price"]) or float(q.get("ltp", entry)))

    if side == "SHORT":
        denom = max(0.01, entry - target)
        prog_pct = ((entry - ltp) / denom) * 100.0
        locked_per_unit = entry - sl
        still_protected = (sl < entry) and (ltp < sl)
        gross_mtm = (entry - ltp) * qty
    else:
        denom = max(0.01, target - entry)
        prog_pct = ((ltp - entry) / denom) * 100.0
        locked_per_unit = sl - entry
        still_protected = (sl > entry) and (ltp > sl)
        gross_mtm = (ltp - entry) * qty

    charges = estimate_roundtrip_charges(entry, ltp, qty, desk=desk)
    if t["status"] == "OPEN":
        mtm_gross = round(gross_mtm, 2)
        mtm_net = round(gross_mtm - charges, 2)
    else:
        mtm_gross = round(float(t.get("gross_pnl", t["pnl"])), 2)
        mtm_net = round(float(t["pnl"]), 2)

    if still_protected and locked_per_unit > 0.01:
        locked_rs = int(round(locked_per_unit * qty))
        protection_label = f"Locked +₹{locked_rs:,}"
        is_locked = True
    elif locked_per_unit > 0.01 and not still_protected:
        protection_label = "Stop Breached"
        is_locked = False
    else:
        protection_label = "At risk"
        is_locked = False

    t["ltp"] = round(ltp, 2)
    t["prog_pct"] = round(prog_pct, 1)
    t["protection_label"] = protection_label
    t["is_locked"] = is_locked
    t["mtm_gross"] = mtm_gross
    t["mtm_pnl"] = mtm_net
    t["est_charges"] = charges
    return t


def _default_local_state() -> Dict[str, Any]:
    return {
        "accounts": {
            "swing_cash": 1000000.0,
            "swing_peak_pnl": 0.0,
            "eq_cash": 100000.0,
            "fno_cash": 100000.0,
            "comm_cash": 100000.0,
            "combined_peak_pnl": 0.0,
            "locked_floor": 0.0,
            "peak_date": "",
            "desk_settings": "",
        },
        "trades": [],
        "agent_messages": [],
    }


def _load_local() -> Dict[str, Any]:
    if not LOCAL_LEDGER_PATH.exists():
        LOCAL_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        seed = _default_local_state()
        LOCAL_LEDGER_PATH.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        return seed
    raw = json.loads(LOCAL_LEDGER_PATH.read_text(encoding="utf-8"))
    if "trades" not in raw:
        raw["trades"] = raw.get("positions", [])
    if "agent_messages" not in raw:
        raw["agent_messages"] = raw.get("agent_chat", [])
    if "accounts" not in raw:
        raw["accounts"] = _default_local_state()["accounts"]
    return raw


def _save_local(state: Dict[str, Any]) -> None:
    LOCAL_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_LEDGER_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def clear_ledger_cache() -> None:
    try:
        if st is not None and hasattr(st, "cache_data"):
            st.cache_data.clear()
    except Exception:
        pass


class LedgerStore:
    """Single-ledger store keeping all desks (Swing, Intraday, F&O, Commodity), balances, and 5-agent chat in one place."""

    def __init__(self) -> None:
        self.master_sh, self.status_msg = _connect_master_sheet()
        self.swing_sh = self.master_sh
        self.unified_sh = self.master_sh
        if self.master_sh is not None:
            try:
                sid = getattr(self.master_sh, "id", None) or id(self.master_sh)
                if sid not in _STRUCTURE_CHECKED:                 # once per process, not on every Streamlit rerun
                    note = ensure_sheet_structure(self.master_sh)
                    _STRUCTURE_CHECKED.add(sid)
                    if note:
                        self.status_msg += f" | Sheet setup: {note}"
            except Exception as exc:
                self.status_msg += f" | Header check: {exc}"

    def load_all(self) -> Dict[str, Any]:
        if self.master_sh is not None:
            accounts = _default_local_state()["accounts"]
            raw_trades: List[Dict[str, Any]] = []
            msgs: List[Dict[str, Any]] = []
            try:
                acc_rows = self.master_sh.worksheet("account").get_all_records()
                if acc_rows:
                    r0 = acc_rows[0]
                    accounts["swing_cash"] = _safe_float(r0.get("swing_cash", r0.get("cash_balance", r0.get("cash", 1000000.0))), 1000000.0)
                    accounts["swing_peak_pnl"] = _safe_float(r0.get("swing_peak_pnl", r0.get("peak_portfolio_pnl", 0.0)), 0.0)
                    accounts["eq_cash"] = _safe_float(r0.get("eq_cash", 100000.0), 100000.0)
                    accounts["fno_cash"] = _safe_float(r0.get("fno_cash", 100000.0), 100000.0)
                    accounts["comm_cash"] = _safe_float(r0.get("comm_cash", 100000.0), 100000.0)
                    accounts["desk_settings"] = str(r0.get("desk_settings", "") or "").strip()
                    accounts["combined_peak_pnl"] = _safe_float(r0.get("combined_peak_pnl", 0.0), 0.0)
                    accounts["locked_floor"] = _safe_float(r0.get("locked_floor", 0.0), 0.0)
                    accounts["peak_date"] = str(r0.get("peak_date", "") or "").strip()
            except Exception as exc:
                self.status_msg += f" | Account read: {exc}"

            try:
                tr_rows = self.master_sh.worksheet("trades").get_all_records()
                raw_trades = [_normalize_master_trade(r) for r in tr_rows if str(r.get("symbol", "")).strip()]
            except Exception as exc:
                self.status_msg += f" | Trades read: {exc}"

            try:
                msgs = self.master_sh.worksheet("agent_messages").get_all_records()
            except Exception:
                msgs = []

            # Fetch live quotes ONLY for OPEN trades
            open_syms = [str(t["symbol"]).strip().upper() for t in raw_trades if t["status"] == "OPEN"]
            default_entries = {str(t["symbol"]).strip().upper(): float(t["entry"]) for t in raw_trades if t["status"] == "OPEN"}
            quotes_map = get_live_quotes_batch(open_syms, default_entries=default_entries, force_live=True) if open_syms else {}
            enriched = [enrich_watch_metrics(r, quotes_map=quotes_map) for r in raw_trades]
            return {"source": "google_sheets", "status_msg": self.status_msg, "accounts": accounts, "trades": enriched, "agent_messages": msgs}

        local = _load_local()
        raw_local = [_normalize_master_trade(r) for r in local.get("trades", [])]
        open_syms = [str(t["symbol"]).strip().upper() for t in raw_local if t["status"] == "OPEN"]
        default_entries = {str(t["symbol"]).strip().upper(): float(t["entry"]) for t in raw_local if t["status"] == "OPEN"}
        quotes_map = get_live_quotes_batch(open_syms, default_entries=default_entries, force_live=True) if open_syms else {}
        trades = [enrich_watch_metrics(r, quotes_map=quotes_map) for r in raw_local]
        return {
            "source": "local_snapshot",
            "status_msg": self.status_msg,
            "accounts": local.get("accounts", _default_local_state()["accounts"]),
            "trades": trades,
            "agent_messages": local.get("agent_messages", []),
        }

    def _update_account_state_unlocked(self, updates: Dict[str, Any]) -> None:
        if self.master_sh is not None:
            try:
                ws = self.master_sh.worksheet("account")
                headers = ws.row_values(1)
                batch_cells = []
                for key, val in updates.items():
                    if key in ACCOUNT_HEADERS and key not in headers:
                        ws.update_cell(1, len(headers) + 1, key)   # auto-add the new column
                        headers.append(key)
                    if key in headers:
                        c_idx = headers.index(key) + 1
                        cell_val = str(val) if isinstance(val, str) else round(float(val), 2)
                        batch_cells.append({"range": _a1(2, c_idx), "values": [[cell_val]]})
                if batch_cells:
                    ws.batch_update(batch_cells)
            except Exception as exc:
                self.status_msg += f" | Account write: {exc}"

        local = _load_local()
        local.setdefault("accounts", {}).update(updates)
        _save_local(local)
        clear_ledger_cache()

    def _append_trade_unlocked(self, trade_row: Dict[str, Any]) -> None:
        norm = _normalize_master_trade(trade_row)
        wrote_sheet = False
        if self.master_sh is not None:
            try:
                ws = self.master_sh.worksheet("trades")
                headers = ensure_master_headers(ws)
                row_vals = [norm.get(h, "") for h in headers]
                ws.append_row(row_vals, value_input_option="USER_ENTERED")
                wrote_sheet = True
            except Exception as exc:
                self.status_msg += f" | Trade append: {type(exc).__name__}: {exc}"
                self.last_write_error = f"{type(exc).__name__}: {exc}"

        if not wrote_sheet and self.master_sh is not None:
            raise RuntimeError("Trade NOT recorded - Google Sheets write failed: " + sheets_error_hint(getattr(self, "last_write_error", "")) + f" | {self.status_msg}")
        if not wrote_sheet:
            local = _load_local()
            local.setdefault("trades", []).append(norm)
            _save_local(local)

        notional = round(_cash_blocked(norm["desk"], float(norm["entry"]), int(norm["qty"])), 2)
        cur_accts = self.load_all().get("accounts", {})
        d_low = normalize_desk_key(norm["desk"])
        cash_key = {"swing": "swing_cash", "intraday": "eq_cash", "fno": "fno_cash", "commodity": "comm_cash"}.get(d_low, "swing_cash")
        self.update_account_state({cash_key: max(0.0, float(cur_accts.get(cash_key, 0.0)) - notional)})
        clear_ledger_cache()

    def append_trade(self, *a: Any, **k: Any) -> Any:
        with LEDGER_WRITE_LOCK:
            return self._append_trade_unlocked(*a, **k)

    def update_account_state(self, *a: Any, **k: Any) -> Any:
        with LEDGER_WRITE_LOCK:
            return self._update_account_state_unlocked(*a, **k)

    def sync_watchdog_trades(self, *a: Any, **k: Any) -> Any:
        with LEDGER_WRITE_LOCK:
            return self._sync_watchdog_trades_unlocked(*a, **k)

    def begin_message_buffer(self) -> None:
        """Collect agent messages in memory; flush_messages() writes them in ONE Sheets call (quota + speed)."""
        self._msg_buffer: List[Any] = []

    def discard_messages(self) -> None:
        self._msg_buffer = []

    def flush_messages(self) -> int:
        buf = getattr(self, "_msg_buffer", None) or []
        self._msg_buffer = []
        if not buf:
            return 0
        with LEDGER_WRITE_LOCK:
            if self.master_sh is not None:
                try:
                    ws = self.master_sh.worksheet("agent_messages")
                    for k in range(0, len(buf), 200):
                        ws.append_rows([o for o, _ in buf[k:k + 200]], value_input_option="USER_ENTERED")
                    clear_ledger_cache()
                    return len(buf)
                except Exception as exc:
                    self.status_msg += f" | Message batch append: {exc}"
            local = _load_local()
            local.setdefault("agent_messages", []).extend(m for _, m in buf)
            _save_local(local)
            clear_ledger_cache()
        return len(buf)

    def append_agent_message(self, msg_row: Dict[str, Any], desk: str = "") -> None:
        ist_now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
        ts_val = str(msg_row.get("timestamp") or ist_now)
        if not ts_val.endswith("IST") and not ts_val.endswith("UTC"):
            ts_val = ist_now
        msg_copy = dict(msg_row)
        msg_copy["timestamp"] = ts_val
        if desk:
            msg_copy["desk"] = desk

        ordered = [
            msg_copy.get("cycle_id", ""),
            ts_val,
            msg_copy.get("sender", ""),
            msg_copy.get("recipient", ""),
            msg_copy.get("msg_type", ""),
            msg_copy.get("summary", ""),
            msg_copy.get("payload_json", "{}"),
        ]

        if getattr(self, "_msg_buffer", None) is not None:
            self._msg_buffer.append((ordered, msg_copy))
            return

        if self.master_sh is not None:
            try:
                try:
                    ws = self.master_sh.worksheet("agent_messages")
                except Exception:
                    ws = self.master_sh.add_worksheet(title="agent_messages", rows=1000, cols=7)
                    ws.append_row(["cycle_id", "timestamp", "sender", "recipient", "msg_type", "summary", "payload_json"], value_input_option="USER_ENTERED")
                ws.append_row(ordered, value_input_option="USER_ENTERED")
                clear_ledger_cache()
                return
            except Exception as exc:
                self.status_msg += f" | Message append: {exc}"

        local = _load_local()
        local.setdefault("agent_messages", []).append(msg_copy)
        _save_local(local)
        clear_ledger_cache()

    def _sync_watchdog_trades_unlocked(
        self,
        trades: List[Dict[str, Any]],
        combined_peak_pnl: Optional[float] = None,
        locked_floor: Optional[float] = None,
        peak_date: Optional[str] = None,
        swing_peak_pnl: Optional[float] = None,
    ) -> Dict[str, int]:
        updated_sheets = 0
        updated_local = 0
        cash_credits = {"swing": 0.0, "intraday": 0.0, "fno": 0.0, "commodity": 0.0}

        if self.master_sh is not None and trades:
            try:
                ws = self.master_sh.worksheet("trades")
                headers = ensure_master_headers(ws)
                records = ws.get_all_records()
                batch_cells: List[Dict[str, Any]] = []

                for tr in trades:
                    sym = str(tr.get("symbol", "")).strip().upper()
                    ts_key = str(tr.get("timestamp", "")).strip()
                    matched_idx = None
                    matched_rec: Dict[str, Any] = {}
                    for idx, rec in enumerate(records, start=2):
                        if str(rec.get("symbol", "")).strip().upper() == sym and ts_key and str(rec.get("timestamp", "")).strip() == ts_key:
                            matched_idx = idx
                            matched_rec = rec
                            break
                    if matched_idx is None:
                        for idx, rec in enumerate(records, start=2):
                            if str(rec.get("symbol", "")).strip().upper() == sym and str(rec.get("status", "")).strip().upper() == "OPEN":
                                matched_idx = idx
                                matched_rec = rec
                                break
                    if matched_idx is None:
                        continue

                    was_open = str(matched_rec.get("status", "")).strip().upper() == "OPEN"
                    now_closed = str(tr.get("status", "")).strip().upper() != "OPEN"
                    if was_open and now_closed:
                        d_key = normalize_desk_key(_canonical_desk(tr.get("desk"), sym, tr.get("book")))
                        if d_key in cash_credits:
                            cash_credits[d_key] += _cash_blocked(d_key, float(tr.get("entry", 0.0)), int(tr.get("qty", 0))) + float(tr.get("pnl", 0.0))

                    preserved_init_sl = matched_rec.get("initial_sl") if str(matched_rec.get("initial_sl", "")).strip() != "" else tr.get("initial_sl", "")
                    patch_map = {
                        "status": tr.get("status", matched_rec.get("status", "OPEN")),
                        "exit_timestamp": tr.get("exit_timestamp", matched_rec.get("exit_timestamp", "")),
                        "initial_sl": preserved_init_sl,
                        "sl": tr.get("sl", matched_rec.get("sl", "")),
                        "target": tr.get("target", matched_rec.get("target", "")),
                        "initial_target": (matched_rec.get("initial_target") if str(matched_rec.get("initial_target", "")).strip() != "" else tr.get("initial_target", "")),
                        "exit_price": tr.get("exit_price", matched_rec.get("exit_price", "")),
                        "gross_pnl": tr.get("gross_pnl", matched_rec.get("gross_pnl", "")),
                        "charges_slippage": tr.get("charges_slippage", matched_rec.get("charges_slippage", "")),
                        "pnl": tr.get("pnl", matched_rec.get("pnl", "")),
                        "net_pnl": tr.get("pnl", matched_rec.get("net_pnl", "")),
                        "realized_r": tr.get("realized_r", matched_rec.get("realized_r", "")),
                        "mfe_pct": tr.get("mfe_pct", matched_rec.get("mfe_pct", "")),
                        "mae_pct": tr.get("mae_pct", matched_rec.get("mae_pct", "")),
                        "reason": tr.get("reason", matched_rec.get("reason", "")),
                    }
                    for col_name, val in patch_map.items():
                        if col_name in headers and val != "":
                            c_idx = headers.index(col_name) + 1
                            batch_cells.append({"range": _a1(matched_idx, c_idx), "values": [[val]]})
                    updated_sheets += 1

                if batch_cells:
                    ws.batch_update(batch_cells)
            except Exception as exc:
                self.status_msg += f" | Watchdog sync: {exc}"

        if self.master_sh is None:
            local = _load_local()
            by_key = {(str(t.get("symbol", "")).strip().upper(), str(t.get("timestamp", "")).strip()): dict(t) for t in trades}
            new_local_trades = []
            for lt in local.get("trades", []):
                k = (str(lt.get("symbol", "")).strip().upper(), str(lt.get("timestamp", "")).strip())
                if k in by_key:
                    was_open = str(lt.get("status", "")).strip().upper() == "OPEN"
                    merged = dict(lt)
                    preserved_init = lt.get("initial_sl") or by_key[k].get("initial_sl")
                    preserved_tgt = lt.get("initial_target") or by_key[k].get("initial_target")
                    merged.update(by_key[k])
                    if preserved_init:
                        merged["initial_sl"] = preserved_init
                    if preserved_tgt:
                        merged["initial_target"] = preserved_tgt
                    if was_open and str(merged.get("status", "")).strip().upper() != "OPEN":
                        d_key = normalize_desk_key(_canonical_desk(merged.get("desk"), merged.get("symbol"), merged.get("book")))
                        if d_key in cash_credits:
                            cash_credits[d_key] += _cash_blocked(d_key, float(merged.get("entry", 0.0)), int(merged.get("qty", 0))) + float(merged.get("pnl", 0.0))
                    new_local_trades.append(merged)
                    updated_local += 1
                else:
                    new_local_trades.append(lt)
            local["trades"] = new_local_trades
            _save_local(local)

        acct_updates: Dict[str, Any] = {}
        cur_accts = self.load_all().get("accounts", {})
        if cash_credits["swing"] > 0:
            acct_updates["swing_cash"] = float(cur_accts.get("swing_cash", 1000000.0)) + cash_credits["swing"]
        if cash_credits["intraday"] > 0:
            acct_updates["eq_cash"] = float(cur_accts.get("eq_cash", 100000.0)) + cash_credits["intraday"]
        if cash_credits["fno"] > 0:
            acct_updates["fno_cash"] = float(cur_accts.get("fno_cash", 100000.0)) + cash_credits["fno"]
        if cash_credits["commodity"] > 0:
            acct_updates["comm_cash"] = float(cur_accts.get("comm_cash", 0.0)) + cash_credits["commodity"]
        if combined_peak_pnl is not None:
            acct_updates["combined_peak_pnl"] = float(combined_peak_pnl)
        if locked_floor is not None:
            acct_updates["locked_floor"] = float(locked_floor)
        if peak_date:
            acct_updates["peak_date"] = str(peak_date)
        if swing_peak_pnl is not None:
            acct_updates["swing_peak_pnl"] = float(swing_peak_pnl)
        if acct_updates:
            self.update_account_state(acct_updates)

        clear_ledger_cache()
        return {"updated_sheets": updated_sheets, "updated_local": updated_local}

    def close_paper_position(self, symbol: str, timestamp: str = "") -> Dict[str, Any]:
        state = self.load_all()
        target_row = next((t for t in state["trades"] if t["status"] == "OPEN" and t["symbol"] == symbol and (not timestamp or str(t.get("timestamp")) == str(timestamp))), None)
        if target_row is None:
            return {"closed": False, "reason": f"No open position found for {symbol}"}

        ist_now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
        exit_px = target_row["ltp"]
        qty = target_row["qty"]
        entry = target_row["entry"]
        init_sl = target_row["initial_sl"] or target_row["sl"]
        risk_unit = max(0.05, abs(entry - init_sl))
        direction = -1.0 if target_row["side"] == "SHORT" else 1.0
        gross = round((exit_px - entry) * qty * direction, 2)
        charges = estimate_roundtrip_charges(entry, exit_px, qty, desk=target_row["desk"])
        net_pnl = round(gross - charges, 2)
        realized_r = round(((exit_px - entry) * direction) / risk_unit, 2)

        updated_row = dict(target_row)
        updated_row.update({
            "status": "CLOSED", "exit_timestamp": ist_now, "exit_price": exit_px,
            "gross_pnl": gross, "charges_slippage": charges, "pnl": net_pnl, "net_pnl": net_pnl, "realized_r": realized_r,
            "reason": f"{target_row.get('reason', '')} [MANUAL_EXIT @ ₹{exit_px:.2f} | Net ₹{net_pnl:+,.2f} ({realized_r:+.2f}R)]".strip(),
        })
        self.sync_watchdog_trades([updated_row])
        return {"closed": True, "symbol": symbol, "net_pnl": net_pnl, "realized_r": realized_r}


def load_ledger_cached(ttl_seconds: int = 20) -> Dict[str, Any]:
    if st is not None and hasattr(st, "cache_data"):
        @st.cache_data(ttl=ttl_seconds, show_spinner=False)
        def _cached_load(_nonce: int = 0) -> Dict[str, Any]:
            return LedgerStore().load_all()
        return _cached_load(0)
    return LedgerStore().load_all()
