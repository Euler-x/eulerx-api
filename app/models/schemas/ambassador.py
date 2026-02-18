import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AmbassadorRank


class AmbassadorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    rank: AmbassadorRank
    referral_code: str
    team_size: int
    total_referrals: int
    rewards_earned: float
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardEntry(BaseModel):
    rank_position: int
    user_id: uuid.UUID
    ambassador_rank: AmbassadorRank
    total_referrals: int
    rewards_earned: float


class ReferralResponse(BaseModel):
    referral_code: str
    referral_link: str
