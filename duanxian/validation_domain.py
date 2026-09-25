"""V0 domain language for the review -> validation -> calibration loop.

These models deliberately sit above the existing collectors.  They make the
public contract explicit without forcing the old review pipeline to migrate in
one risky change.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SnapshotType(str, Enum):
    previous_close = "previous_close"
    auction_0925 = "auction_0925"
    morning_1000 = "morning_1000"
    midday_1130 = "midday_1130"
    late_1430 = "late_1430"
    close_1500 = "close_1500"


class ValidationStatus(str, Enum):
    pending = "pending"
    watching = "watching"
    satisfied = "satisfied"
    invalidated = "invalidated"
    insufficient_data = "insufficient_data"
    expired = "expired"


class SourceState(BaseModel):
    name: str
    available: bool
    observed_at: datetime | None = None
    reason: str = ""


class TradingSnapshot(BaseModel):
    schema_version: int = 1
    trade_date: str
    type: SnapshotType
    captured_at: datetime
    market_breadth: dict[str, Any] = Field(default_factory=dict)
    pools: dict[str, Any] = Field(default_factory=dict)
    ladder: dict[str, Any] = Field(default_factory=dict)
    themes: list[dict[str, Any]] = Field(default_factory=list)
    core_stocks: list[dict[str, Any]] = Field(default_factory=list)
    coverage: float = Field(default=0, ge=0, le=1)
    sources: list[SourceState] = Field(default_factory=list)


class MarketRegime(BaseModel):
    phase: str | None = None
    risk_level: Literal["low", "medium", "high", "unknown"] = "unknown"
    confidence: Literal["high", "medium", "low"] = "low"
    coverage: float = Field(default=0, ge=0, le=1)
    dimensions: dict[str, float | None] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ThemeState(BaseModel):
    name: str
    lifecycle: str = "待确认"
    strength: float | None = None
    diffusion: str = "数据不足"
    core_stocks: list[dict[str, Any]] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class StockRole(BaseModel):
    code: str
    name: str
    role: Literal["高度核心", "题材核心", "容量核心", "补涨", "跟随", "风险样本", "角色待确认"]
    theme: str | None = None
    frozen_at: datetime | None = None


class ValidationRule(BaseModel):
    id: str
    prediction_date: str
    judgment: str
    metric: str
    label: str
    baseline: float | None = None
    expected_direction: Literal["上升", "下降", "持平"]
    threshold: float
    earliest_snapshot: SnapshotType = SnapshotType.auction_0925
    expires_at: datetime
    rationale: str


class ValidationTransition(BaseModel):
    status: ValidationStatus
    at: datetime
    snapshot: SnapshotType
    value: float | None = None
    evidence: str = ""


class ValidationResult(BaseModel):
    rule: ValidationRule
    status: ValidationStatus
    current_value: float | None = None
    actual_direction: str | None = None
    transitions: list[ValidationTransition] = Field(default_factory=list)


class MarketEvent(BaseModel):
    id: str
    kind: str
    title: str
    occurred_at: datetime
    severity: Literal["info", "watch", "risk"] = "info"
    before: Any = None
    after: Any = None
    threshold: float | None = None
    scope: list[str] = Field(default_factory=list)
    evidence: str = ""
