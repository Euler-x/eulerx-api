from typing import Optional

from pydantic import BaseModel


class SpotBalance(BaseModel):
    coin: str
    total: float
    hold: float = 0.0


class ProofOfReservesResponse(BaseModel):
    on_chain_balance: float = 0.0
    perps_balance: float = 0.0
    spot_balance: float = 0.0
    spot_balances: list[SpotBalance] = []
    total_allocated: float = 0.0
    margin_used: float = 0.0
    free_collateral: float = 0.0
    surplus_deficit: float = 0.0
    verification_timestamp: str = ""


class LivePositionResponse(BaseModel):
    symbol: str
    size: float
    entry_price: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: Optional[float] = None
    margin_used: float = 0.0
    position_value: float = 0.0


class AgentKeyPermissions(BaseModel):
    wallet_type: str
    can_trade: bool
    can_withdraw: bool
    can_transfer: bool
    can_modify_agent: bool
    description: str
    verified: bool


class WalletInfoResponse(BaseModel):
    wallet_address: Optional[str] = None
    explorer_link: Optional[str] = None
    agent_permissions: Optional[AgentKeyPermissions] = None
    smart_contract_audit_link: str = "https://docs.eulerx.io/security/audit"
