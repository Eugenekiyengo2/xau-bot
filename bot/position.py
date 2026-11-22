"""Turn a risk percentage into an actual order size.

A signal that says "stop at 4046" is only half an instruction; the other half is how
many lots. That depends on the account, the chosen risk, and the contract size of the
instrument, and getting it wrong is how a correct trade still blows up an account.

Contract sizes are the standard ones. Check them against your own broker before
trading — they vary, particularly for metals and indices.
"""
from __future__ import annotations

# units per 1.00 lot, i.e. profit per 1.0 of price movement per lot
CONTRACT = {
    "XAU/USD": 100.0,      # 100 ounces
    "XAG/USD": 5000.0,     # 5,000 ounces
    "EUR/USD": 100000.0,
    "GBP/USD": 100000.0,
    "AUD/USD": 100000.0,
    "NZD/USD": 100000.0,
    "USD/JPY": 100000.0,   # quoted in JPY: see note in size()
    "USD/CHF": 100000.0,
    "USD/CAD": 100000.0,
}
DEFAULT_CONTRACT = 100000.0
MIN_LOT = 0.01


def size(symbol: str, entry: float, stop: float, balance: float, risk_pct: float) -> dict:
    """How many lots risks exactly `risk_pct` of `balance` if the stop is hit."""
    stop_distance = abs(entry - stop)
    risk_cash = balance * risk_pct
    if stop_distance <= 0 or risk_cash <= 0:
        return {"lots": 0.0, "risk_cash": 0.0, "note": "no valid stop distance"}

    per_lot_per_point = CONTRACT.get(symbol.upper(), DEFAULT_CONTRACT)
    # For a pair quoted in the counter currency (USD/JPY and friends) the value of a
    # move is in that currency, so converting back to USD divides by the rate.
    if symbol.upper().startswith("USD/"):
        per_lot_per_point = per_lot_per_point / max(entry, 1e-9)

    loss_per_lot = stop_distance * per_lot_per_point
    lots = risk_cash / loss_per_lot if loss_per_lot else 0.0

    note = None
    if lots < MIN_LOT:
        note = (f"{lots:.4f} lots is below the {MIN_LOT} minimum — this trade cannot be "
                f"taken at {risk_pct:.1%} on a ${balance:,.0f} account. "
                f"The smallest allowed size risks "
                f"${MIN_LOT*loss_per_lot:,.2f} ({MIN_LOT*loss_per_lot/balance:.1%}).")
        lots = MIN_LOT
    else:
        lots = round(lots / MIN_LOT) * MIN_LOT

    actual = lots * loss_per_lot
    notional = lots * CONTRACT.get(symbol.upper(), DEFAULT_CONTRACT) * entry
    return {
        "lots": round(lots, 2),
        "risk_cash": round(actual, 2),
        "risk_pct_actual": actual / balance if balance else 0.0,
        "notional": round(notional, 2),
        "stop_distance": round(stop_distance, 2),
        "note": note,
    }


def margin_note(notional: float, balance: float, leverage: int = 500) -> str | None:
    """Flag when the position needs an uncomfortable share of the account as margin."""
    if not balance or not notional:
        return None
    need = notional / max(leverage, 1)
    share = need / balance
    if share >= 0.5:
        return (f"Needs ~${need:,.0f} margin at 1:{leverage} — {share:.0%} of the account "
                f"tied up in one position. A second trade may be refused, and a modest "
                f"adverse move risks a margin call before your stop is reached.")
    if share >= 0.2:
        return f"Needs ~${need:,.0f} margin at 1:{leverage} ({share:.0%} of the account)."
    return None
