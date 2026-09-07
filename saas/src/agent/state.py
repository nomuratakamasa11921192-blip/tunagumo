from typing import Annotated, Literal, TypedDict


def merge_dict(l: dict | None, r: dict | None) -> dict:
    return {**(l or {}), **(r or {})}


def extend_list(l: list | None, r: list | None) -> list:
    return (l or []) + (r or [])


def add_float(l: float | None, r: float | None) -> float:
    return (l or 0.0) + (r or 0.0)


def add_int(l: int | None, r: int | None) -> int:
    return (l or 0) + (r or 0)


def keep_last(l, r):
    """reducerが無い(単一値)フィールドは、同じステップ内で2回書き込まれると
    LangGraphがInvalidUpdateErrorで落ちる(実際に低速なネットワーク環境で再現した)。
    「最後に書き込まれた値を採用する」ことを明示することで、想定外の同時書き込みが
    発生してもクラッシュしないようにする防御的なreducer。"""
    return r


class OrgState(TypedDict):
    tenant_id: str
    session_id: str
    requester_id: str
    raw_message: str
    goal: Annotated[str, keep_last]
    clarifications: dict
    clarify_count: Annotated[int, keep_last]
    board: Annotated[dict, merge_dict]
    citations: Annotated[list, extend_list]
    qa_findings: Annotated[list, extend_list]
    qa_verdict: Annotated[Literal["PASS", "FAIL", "PENDING"], keep_last]
    qa_warnings: Annotated[list, extend_list]
    approval_summary: Annotated[dict, keep_last]
    # 以下2つは仕様書のOrgStateに明記はないが、ノード間で情報を渡すために必要な補完フィールド
    instruction: Annotated[str, keep_last]  # 統括AIが各部署に出す具体的な指示(2-6 instructionの伝達用)
    pending_questions: Annotated[list[str], keep_last]  # clarifyノードが画面に表示する質問(2-4)
    next_depts: Annotated[list[str], keep_last]  # ceo_officeが選んだ次の部署(条件付きエッジ用)
    rejection_count: Annotated[int, keep_last]
    rejection_comment: Annotated[str, keep_last]
    rejection_history: Annotated[list, extend_list]
    step_history: Annotated[list, extend_list]
    retry_counts: Annotated[dict, keep_last]
    # 仕様書のOrgStateでは reducer なしの int/str だが、低速なネットワーク環境下で
    # 同じステップ内に2回書き込みが発生し InvalidUpdateError で実際に落ちたため、
    # 単一値のフィールドすべてに keep_last を付けている（実装上の補完）。
    total_steps: Annotated[int, add_int]
    cost_usd: Annotated[float, add_float]
    status: Annotated[
        Literal[
            "CLARIFYING",
            "RUNNING",
            "AWAITING_APPROVAL",
            "APPROVED",
            "REJECTED",
            "ERROR",
            "HALTED",
            "COMPLETED",
        ],
        keep_last,
    ]
    error_message: Annotated[str, keep_last]


def new_state(tenant_id: str, session_id: str, requester_id: str, raw_message: str) -> OrgState:
    return OrgState(
        tenant_id=tenant_id,
        session_id=session_id,
        requester_id=requester_id,
        raw_message=raw_message,
        goal="",
        clarifications={},
        clarify_count=0,
        board={},
        citations=[],
        qa_findings=[],
        qa_verdict="PENDING",
        qa_warnings=[],
        approval_summary={},
        instruction="",
        pending_questions=[],
        next_depts=[],
        rejection_count=0,
        rejection_comment="",
        rejection_history=[],
        step_history=[],
        retry_counts={},
        total_steps=0,
        cost_usd=0.0,
        status="RUNNING",
        error_message="",
    )
