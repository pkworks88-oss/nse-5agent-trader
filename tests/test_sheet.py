"""Simulates the user's real Ledger.xlsx as a Google Sheet (gspread-like fake) and runs the ledger through a full trade lifecycle."""
import os, sys, re, json, warnings, tempfile
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())
import openpyxl
from core import ledger as L

def a1(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref); col = 0
    for ch in m.group(1): col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col
class WS:
    def __init__(s, title, grid): s.title, s.grid = title, grid; s.col_count = 40
    def _ensure(s, r, c):
        while len(s.grid) < r: s.grid.append([])
        while len(s.grid[r-1]) < c: s.grid[r-1].append("")
    def row_values(s, r): 
        row = s.grid[r-1] if r <= len(s.grid) else []
        while row and row[-1] in ("", None): row = row[:-1]
        return ["" if v is None else v for v in row]
    def update(s, range_name=None, values=None, **k):
        r0, c0 = a1(range_name)
        for i, row in enumerate(values):
            for j, v in enumerate(row): s._ensure(r0+i, c0+j); s.grid[r0+i-1][c0+j-1] = v
    def update_cell(s, r, c, v): s._ensure(r, c); s.grid[r-1][c-1] = v
    def append_row(s, vals, **k): s.grid.append(list(vals))
    def batch_update(s, cells):
        for c in cells: s.update(range_name=c["range"], values=c["values"])
    def add_cols(s, n): s.col_count += n
    def get_all_records(s):
        if not s.grid: return []
        h = s.row_values(1)
        return [{h[j]: (row[j] if j < len(row) else "") for j in range(len(h))} for row in s.grid[1:] if any(v not in ("", None) for v in row)]
class Sheet:
    id = "fake-sheet"; title = "Ledger"
    def __init__(s, wss): s.wss = {w.title: w for w in wss}
    def worksheet(s, n):
        if n not in s.wss: raise Exception("WorksheetNotFound " + n)
        return s.wss[n]
    def add_worksheet(s, title, rows, cols): s.wss[title] = WS(title, []); return s.wss[title]

wb = openpyxl.load_workbook("/mnt/user-data/uploads/Ledger.xlsx")
wss = [WS(w.title, [[c for c in row] for row in w.iter_rows(values_only=True) if any(c is not None for c in row)]) for w in wb.worksheets]
sheet = Sheet(wss)
L._connect_master_sheet = lambda: (sheet, "Single Ledger Synced: Ledger")

store = L.LedgerStore()
print("status:", store.status_msg)
acc = sheet.worksheet("account")
assert acc.row_values(1)[:3] == ["swing_cash", "swing_peak_pnl", "eq_cash"] and float(acc.row_values(2)[0]) == 1000000.0
assert sheet.worksheet("agent_messages").row_values(1) == L.MESSAGE_HEADERS
assert "initial_target" in sheet.worksheet("trades").row_values(1)

# lifecycle
store.append_agent_message({"cycle_id": "c1", "sender": "AG4", "recipient": "AG5", "msg_type": "TEST", "summary": "hello", "payload_json": "{}"}, desk="Swing")
assert sheet.worksheet("agent_messages").row_values(2)[2] == "AG4"           # message is DATA row 2, header intact
store.append_trade({"timestamp": "2026-11-25 10:00:00 IST", "desk": "F&O", "symbol": "NIFTY 25000 PE", "status": "OPEN", "qty": 65, "lot_size": 65,
                    "entry": 170.0, "initial_sl": 136.0, "sl": 136.0, "target": 241.0, "initial_target": 241.0, "side": "LONG", "tier": "HIGH", "sector": "INDEX"})
st = store.load_all()
assert st["source"] == "google_sheets" and len(st["trades"]) == 1 and st["trades"][0]["desk"] == "F&O"
assert abs(st["accounts"]["fno_cash"] - (100000 - 170 * 65)) < 1, st["accounts"]
store.update_account_state({"desk_settings": json.dumps({"fno": {"capital": 250000}})})
from core.config import get_effective_desk_config
assert get_effective_desk_config("fno", L.LedgerStore().load_all()["accounts"]).capital == 250000
tr = dict(st["trades"][0]); tr.update({"status": "TARGET_HIT", "exit_price": 241.0, "pnl": 4500.0, "exit_timestamp": "2026-11-25 12:00:00 IST", "target": 241.0})
store.sync_watchdog_trades([tr], combined_peak_pnl=4500.0, locked_floor=3000.0, peak_date="2026-11-25")
st2 = L.LedgerStore().load_all()
assert st2["trades"][0]["status"] == "TARGET_HIT" and st2["accounts"]["combined_peak_pnl"] == 4500.0 and st2["accounts"]["peak_date"] == "2026-11-25"
assert st2["accounts"]["fno_cash"] > 100000, st2["accounts"]
print("SHEET TEST PASSED  | account row:", dict(zip(acc.row_values(1), acc.row_values(2))))
