from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.ambassador import Ambassador
from app.models.schemas.ambassador import (
    AmbassadorResponse,
    LeaderboardEntry,
    ReferralResponse,
)
from app.models.user import User
from app.utils.helpers import generate_referral_code

router = APIRouter(prefix="/ambassador", tags=["Ambassador"])


@router.get("", response_model=AmbassadorResponse | None)
async def get_ambassador_dashboard(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).where(Ambassador.user_id == current_user.id)
    )
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        return None
    return AmbassadorResponse.model_validate(ambassador)


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).order_by(Ambassador.total_referrals.desc()).limit(limit)
    )
    ambassadors = result.scalars().all()

    return [
        LeaderboardEntry(
            rank_position=idx + 1,
            user_id=amb.user_id,
            ambassador_rank=amb.rank,
            total_referrals=amb.total_referrals,
            rewards_earned=float(amb.rewards_earned),
        )
        for idx, amb in enumerate(ambassadors)
    ]


@router.post("/referral", response_model=ReferralResponse)
async def generate_referral(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).where(Ambassador.user_id == current_user.id)
    )
    ambassador = result.scalar_one_or_none()

    if ambassador is None:
        code = generate_referral_code()
        ambassador = Ambassador(
            user_id=current_user.id,
            referral_code=code,
        )
        db.add(ambassador)
        await db.flush()

    return ReferralResponse(
        referral_code=ambassador.referral_code,
        referral_link=f"https://eulerx.network/ref/{ambassador.referral_code}",
    )


@router.get("/team", response_model=list[AmbassadorResponse])
async def get_team(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).where(Ambassador.user_id == current_user.id)
    )
    ambassador = result.scalar_one_or_none()

    if ambassador is None:
        return []

    team_result = await db.execute(
        select(Ambassador).where(Ambassador.referred_by == ambassador.id)
    )
    team = team_result.scalars().all()
    return [AmbassadorResponse.model_validate(t) for t in team]
