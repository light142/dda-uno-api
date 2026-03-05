"""Pydantic schemas for simulation endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, Any


class SimulationCreate(BaseModel):
    mode: str = Field(..., pattern="^(single|adaptive)$")
    seat0: str = "casual"
    seat1: Optional[str] = "selfish"
    seat2: Optional[str] = "selfish"
    seat3: Optional[str] = "selfish"
    games: int = Field(1000, ge=100, le=50000)
    target_win_rate: Optional[float] = Field(0.25, ge=0.05, le=0.80)


class SimulationOut(BaseModel):
    id: str
    mode: str
    status: str
    config: Any
    games_total: int
    games_done: int
    final_win_rate: Optional[float] = None
    created_at: str
    finished_at: Optional[str] = None

    class Config:
        from_attributes = True
