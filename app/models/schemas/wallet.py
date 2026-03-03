from typing import Optional

from pydantic import BaseModel


class WalletBalanceResponse(BaseModel):
    """Lightweight wallet balance for dashboard display."""

    has_wallet: bool = False
    account_equity: float = 0.0
    available_balance: float = 0.0
    margin_used: float = 0.0
    unrealized_pnl: float = 0.0
    spot_balance: float = 0.0
    total_balance: float = 0.0
    open_positions: int = 0
    wallet_address_masked: Optional[str] = None
    last_synced: Optional[str] = None
