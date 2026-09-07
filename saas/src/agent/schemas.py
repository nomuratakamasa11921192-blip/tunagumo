from typing import Literal

from pydantic import BaseModel, Field


class Triage(BaseModel):
    intent: Literal["GREETING", "FAQ", "WORK_REQUEST"]
    goal: str | None = None
    reply: str | None = None
    reason: str


class SupervisorDecision(BaseModel):
    verdict: Literal["CLARIFY", "ASSIGN", "REVISE", "SUMMARIZE"]
    target_depts: list[str] = Field(default_factory=list)
    instruction: str = ""
    missing_info: list[str] = Field(default_factory=list)
    reason: str


class QualityFinding(BaseModel):
    criterion: Literal[
        "SPECIFICITY",
        "DIFFERENTIATION",
        "ACTIONABILITY",
        "CONSISTENCY",
        "AUDIENCE_FIT",
    ]
    verdict: Literal["PASS", "FAIL"]
    evidence: str
    reason: str


class FactCheckFinding(BaseModel):
    dept_id: str
    verdict: Literal["PASS", "FAIL"]
    issue: str
    evidence: str


class QaResult(BaseModel):
    fact_check: list[FactCheckFinding] = Field(default_factory=list)
    quality_findings: list[QualityFinding] = Field(default_factory=list)


class ApprovalSummary(BaseModel):
    headline: str
    deliverables: list[str] = Field(default_factory=list)
    qa_result: str
    decision_points: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
