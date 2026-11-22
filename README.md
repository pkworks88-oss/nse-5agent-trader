# NSE Multi-Asset AI Trading Platform — V4.8

Consolidated multi-desk paper trading and quantitative audit architecture connected to both **`NSE_5Agent_Ledger`** (Positional Swing) and **`NSE_Unified_2L_Ledger`** (Intraday Equity + Index F&O), plus an optional user-configured **Commodity** desk.

## Capital & Desk Reconciliation
- **Swing Desk (`NSE_5Agent_Ledger`):** Initial ₹10,00,000 | Sheet Free Cash `account!A2` (`₹41,210.02`) + Open Notional (`₹9,57,138.98`) + Realized (`-₹1,651.00`) = `₹10,00,000.00`
- **Intraday Equity (`NSE_Unified_2L_Ledger`):** Initial ₹1,00,000 | Sheet `eq_cash` (`₹1,02,804.73`) = Initial ₹1,00,000 + Realized (`+₹2,804.73`)
- **Index F&O (`NSE_Unified_2L_Ledger`):** Initial ₹1,00,000 | Sheet `fno_cash` (`₹1,10,676.95`) = Initial ₹1,00,000 + Realized (`+₹10,676.95`)
- **Commodity Desk (`NSE_Unified_2L_Ledger`):** User-editable capital (`₹0` initial by default until user allocates), routed to `NSE_Unified_2L_Ledger` alongside Intraday and F&O
