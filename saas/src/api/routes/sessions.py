import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.config_models import AppConfig
from src.agent.llm import StructuredLLM
from src.agent.runner import (
    fire_and_forget,
    run_new_session,
    submit_approval_decision,
    submit_clarify_answer,
)
from src.api.deps import (
    get_app_config,
    get_checkpointer,
    get_current_tenant,
    get_embedding_provider,
    get_higgsfield_client,
    get_llm,
    get_optional_tenant_user,
    get_scoped_db,
)
from src.core.ai_budget import (
    ESTIMATED_HIGGSFIELD_IMAGE_COST_USD,
    ESTIMATED_HIGGSFIELD_VIDEO_COST_USD,
    BudgetExceededError,
    ensure_budget_available,
    record_cost,
)
from src.core.flyer_generator import FlyerData, FlyerGenerationError, generate_flyer_pdf
from src.core.higgsfield_client import HiggsfieldClient, HiggsfieldError
from src.core.models import AuditLog, Session, Tenant, TenantUser
from src.core.models import Approval as ApprovalModel
from src.rag.embeddings import EmbeddingProvider

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    text: str
    idempotency_key: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: uuid.UUID
    status: str


class SessionDetailResponse(BaseModel):
    session_id: uuid.UUID
    status: str
    request_text: str
    result: dict | None
    created_at: datetime
    updated_at: datetime


class SessionSummary(BaseModel):
    session_id: uuid.UUID
    status: str
    request_text: str
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class ApproveRequest(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


class AnswerRequest(BaseModel):
    answer: dict | str


async def _get_owned_session(db: AsyncSession, tenant: Tenant, session_id: uuid.UUID) -> Session:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.tenant_id == tenant.id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        # 別テナントのセッションIDでも、存在有無を漏らさず404にする
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    return session


@router.post("", response_model=CreateSessionResponse, status_code=202)
async def create_session(
    req: CreateSessionRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    app_config: AppConfig = Depends(get_app_config),
    llm: StructuredLLM = Depends(get_llm),
    embedding_provider: EmbeddingProvider | None = Depends(get_embedding_provider),
    checkpointer=Depends(get_checkpointer),
) -> CreateSessionResponse:
    # tenant(get_current_tenant)はdb(get_scoped_db)とは別のDBセッションに属するため、
    # dbに紐づく行を取り直してからチェックする(generate-videoの月間動画枠チェックと同じ注意)。
    tenant_row = await db.get(Tenant, tenant.id)
    try:
        ensure_budget_available(tenant_row)
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))

    if req.idempotency_key:
        existing = await db.execute(
            select(Session).where(
                Session.tenant_id == tenant.id,
                Session.idempotency_key == req.idempotency_key,
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return CreateSessionResponse(session_id=found.id, status=found.status)

    session = Session(
        tenant_id=tenant.id,
        idempotency_key=req.idempotency_key,
        request_text=req.text,
        status="QUEUED",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    fire_and_forget(
        run_new_session(
            tenant_id=str(tenant.id),
            session_id=str(session.id),
            requester_id=str(tenant.id),  # Phase 10でテナント内ユーザーの識別に差し替える
            raw_message=req.text,
            app_config=app_config,
            llm=llm,
            checkpointer=checkpointer,
            embedding_provider=embedding_provider,
        ),
        tenant_id=str(tenant.id),
        session_id=str(session.id),
    )

    return CreateSessionResponse(session_id=session.id, status=session.status)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    limit: int = 50,
) -> SessionListResponse:
    """顧客画面の履歴表示用。自テナントのセッションを新しい順に返す。"""
    result = await db.execute(
        select(Session)
        .where(Session.tenant_id == tenant.id)
        .order_by(Session.created_at.desc())
        .limit(min(limit, 200))
    )
    sessions = result.scalars().all()
    return SessionListResponse(
        sessions=[
            SessionSummary(
                session_id=s.id,
                status=s.status,
                request_text=s.request_text,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in sessions
        ]
    )


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> SessionDetailResponse:
    session = await _get_owned_session(db, tenant, session_id)
    return SessionDetailResponse(
        session_id=session.id,
        status=session.status,
        request_text=session.request_text,
        result=session.result,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


class GenerateImageRequest(BaseModel):
    # 例:「縦長のバナーで」「物件の外観イメージで」など、生成内容への追加の指示(任意)。
    prompt_hint: str | None = None
    # "draft"(既定)は低品質・低クレジットで生成し、"high"は良ければ作り直す用の高画質版。
    quality: Literal["draft", "high"] = "draft"


class GenerateImageResponse(BaseModel):
    image_url: str


class GenerateVideoRequest(BaseModel):
    prompt_hint: str | None = None
    quality: Literal["draft", "high"] = "draft"
    # 物件URL取込で見つかった写真など、動画の起点にする画像(任意)
    reference_image_url: str | None = None


class GenerateVideoResponse(BaseModel):
    video_url: str


def _session_context_text(session: Session) -> str:
    """完成した文章から、生成AIに渡す題材テキストを取り出す(画像・動画で共通)。"""
    result = session.result or {}

    approval_summary = result.get("approval_summary")
    if approval_summary and approval_summary.get("headline"):
        context_text = approval_summary["headline"]
    elif result.get("board"):
        context_text = " / ".join(str(v) for v in result["board"].values())
    elif result.get("reply"):
        context_text = result["reply"]
    else:
        context_text = session.request_text

    # 生成モデルに渡すテキストが長すぎないよう、目安として先頭400文字に絞る
    return context_text[:400]


def _build_image_prompt(session: Session, prompt_hint: str | None) -> str:
    """生成済みの文章から、付随画像用のプロンプトの元ネタを組み立てる。
    英語の指示文で統一しているのは、画像生成モデルへの指示は英語の方が安定するため
    (日本語の題材テキスト自体はそのまま埋め込む)。
    """
    context_text = _session_context_text(session)
    prompt = (
        f"Create a clean, professional supporting image for the following Japanese business content: "
        f'"{context_text}". '
        "Calm, warm, minimal editorial photographic style with soft neutral tones. "
        "No readable text, no logos, no watermark."
    )
    if prompt_hint:
        prompt += f" Additional direction: {prompt_hint}"
    return prompt


def _build_video_prompt(session: Session, prompt_hint: str | None) -> str:
    """生成済みの文章から、付随する短い動画用のプロンプトの元ネタを組み立てる。
    _build_image_promptと同じ理由で指示文は英語に統一している。
    """
    context_text = _session_context_text(session)
    prompt = (
        f"Create a short, clean, professional supporting video clip for the following Japanese "
        f'business content: "{context_text}". '
        "Calm, warm, minimal editorial style with soft neutral tones, subtle natural motion. "
        "No readable text, no logos, no watermark."
    )
    if prompt_hint:
        prompt += f" Additional direction: {prompt_hint}"
    return prompt


@router.post("/{session_id}/generate-image", response_model=GenerateImageResponse, status_code=201)
async def generate_session_image(
    session_id: uuid.UUID,
    req: GenerateImageRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    higgsfield: HiggsfieldClient | None = Depends(get_higgsfield_client),
) -> GenerateImageResponse:
    """完成した文章に付随する画像を生成する(Higgsfield。運営(ツナグモ)自身のHiggsfield
    アカウントを使う。コストは月間AI予算から消費する、src/core/ai_budget.py参照)。
    """
    if higgsfield is None:
        raise HTTPException(
            status_code=402,
            detail="Higgsfield APIキーが運営側で設定されていません(設定不備)。",
        )

    tenant_row = await db.get(Tenant, tenant.id)
    try:
        ensure_budget_available(tenant_row)
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))

    session = await _get_owned_session(db, tenant, session_id)
    if session.status not in ("AWAITING_APPROVAL", "APPROVED", "COMPLETED"):
        raise HTTPException(
            status_code=409,
            detail=f"文章が完成する前は画像を生成できません（現在の状態: {session.status}）",
        )

    prompt = _build_image_prompt(session, req.prompt_hint)

    try:
        image_url = await higgsfield.generate_image(prompt, quality=req.quality)
    except HiggsfieldError as e:
        raise HTTPException(status_code=502, detail=str(e))

    record_cost(tenant_row, ESTIMATED_HIGGSFIELD_IMAGE_COST_USD)

    result = dict(session.result or {})
    generated_images = list(result.get("generated_images", []))
    generated_images.append({"url": image_url, "prompt_hint": req.prompt_hint, "quality": req.quality})
    result["generated_images"] = generated_images
    session.result = result
    await db.commit()

    return GenerateImageResponse(image_url=image_url)


@router.post("/{session_id}/generate-video", response_model=GenerateVideoResponse, status_code=201)
async def generate_session_video(
    session_id: uuid.UUID,
    req: GenerateVideoRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    higgsfield: HiggsfieldClient | None = Depends(get_higgsfield_client),
) -> GenerateVideoResponse:
    """完成した文章に付随する短い動画を生成する(Higgsfield。運営(ツナグモ)自身の
    Higgsfieldアカウントを使う。コストは月間AI予算から消費する、src/core/ai_budget.py
    参照)。動画は生成に数分かかることがあるため、呼び出し元(フロント)はタイムアウトを
    長めに見ておくこと。

    メインのAI生成パイプライン(run_new_session等)と違い、ここはあえて
    fire_and_forget+ポーリングにせず、リクエストを開いたまま待つ同期実装にしている
    (実装を単純に保つため)。asyncio内のawaitで待つだけなので他リクエストは
    ブロックしないが、UXとして「ボタンを押してから数分待つ」体験になる点は
    利用が増えてきたら見直す(バックグラウンドジョブ化する)候補。
    """
    if higgsfield is None:
        raise HTTPException(
            status_code=402,
            detail="Higgsfield APIキーが運営側で設定されていません(設定不備)。",
        )

    # tenant(get_current_tenant)はdb(get_scoped_db)とは別のDBセッションに属するため、
    # ここでのカウント更新をdb.commit()で永続化できるよう、dbに紐づく行を取り直す。
    tenant_row = await db.get(Tenant, tenant.id)
    try:
        ensure_budget_available(tenant_row)
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))

    session = await _get_owned_session(db, tenant, session_id)
    if session.status not in ("AWAITING_APPROVAL", "APPROVED", "COMPLETED"):
        raise HTTPException(
            status_code=409,
            detail=f"文章が完成する前は動画を生成できません（現在の状態: {session.status}）",
        )

    prompt = _build_video_prompt(session, req.prompt_hint)

    try:
        video_url = await higgsfield.generate_video(
            prompt, quality=req.quality, reference_image_url=req.reference_image_url
        )
    except HiggsfieldError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 生成が成功したときだけ予算を消費する(失敗はカウントしない)。
    record_cost(tenant_row, ESTIMATED_HIGGSFIELD_VIDEO_COST_USD)

    result = dict(session.result or {})
    generated_videos = list(result.get("generated_videos", []))
    generated_videos.append({"url": video_url, "prompt_hint": req.prompt_hint, "quality": req.quality})
    result["generated_videos"] = generated_videos
    session.result = result
    await db.commit()

    return GenerateVideoResponse(video_url=video_url)


class GenerateFlyerRequest(BaseModel):
    image_urls: list[str] = []


def _flyer_title_and_body(session: Session) -> tuple[str, str]:
    result = session.result or {}
    board = result.get("board")
    if board:
        body_text = "\n\n".join(str(v) for v in board.values())
    elif result.get("reply"):
        body_text = result["reply"]
    else:
        body_text = session.request_text

    approval_summary = result.get("approval_summary") or {}
    title = approval_summary.get("headline") or "物件のご案内資料"
    return title, body_text


@router.post("/{session_id}/generate-flyer", status_code=200)
async def generate_session_flyer(
    session_id: uuid.UUID,
    req: GenerateFlyerRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> Response:
    """完成した文章と画像から、マイソク(募集図面・チラシ)PDFを生成する(13位)。
    永続化はせず、都度PDFを作ってそのまま返す(既存のgenerate-image/videoと違い、
    session.resultには記録しない — 何度でも作り直せる使い捨ての出力物のため)。
    """
    session = await _get_owned_session(db, tenant, session_id)
    if session.status not in ("AWAITING_APPROVAL", "APPROVED", "COMPLETED"):
        raise HTTPException(
            status_code=409,
            detail=f"文章が完成する前はマイソクPDFを生成できません（現在の状態: {session.status}）",
        )

    title, body_text = _flyer_title_and_body(session)
    try:
        pdf_bytes = generate_flyer_pdf(
            FlyerData(title=title, body_text=body_text, image_urls=req.image_urls)
        )
    except FlyerGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=flyer.pdf"},
    )


@router.post("/{session_id}/answer", status_code=202)
async def answer_session(
    session_id: uuid.UUID,
    req: AnswerRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    app_config: AppConfig = Depends(get_app_config),
    llm: StructuredLLM = Depends(get_llm),
    embedding_provider: EmbeddingProvider | None = Depends(get_embedding_provider),
    checkpointer=Depends(get_checkpointer),
) -> dict:
    """要件確認(clarify)への回答を受け取り、停止していたグラフを再開する(2-4)。"""
    session = await _get_owned_session(db, tenant, session_id)

    if session.status != "CLARIFYING":
        raise HTTPException(
            status_code=409,
            detail=f"要件確認待ちの状態ではありません（現在の状態: {session.status}）",
        )

    fire_and_forget(
        submit_clarify_answer(
            tenant_id=str(tenant.id),
            session_id=str(session.id),
            answer=req.answer,
            app_config=app_config,
            llm=llm,
            checkpointer=checkpointer,
            embedding_provider=embedding_provider,
        ),
        tenant_id=str(tenant.id),
        session_id=str(session.id),
    )

    return {"session_id": session.id, "status": "RUNNING"}


@router.post("/{session_id}/approve", status_code=202)
async def approve_session(
    session_id: uuid.UUID,
    req: ApproveRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    app_config: AppConfig = Depends(get_app_config),
    llm: StructuredLLM = Depends(get_llm),
    embedding_provider: EmbeddingProvider | None = Depends(get_embedding_provider),
    checkpointer=Depends(get_checkpointer),
    actor: TenantUser | None = Depends(get_optional_tenant_user),
) -> dict:
    """承認・却下を受け付ける(6-2)。

    Phase 13(多段階承認): tenant.approval_stagesが2以上のテナントでは、X-User-Token
    (src/api/deps.pyのget_optional_tenant_user)でapprover/ownerロールのTenantUserとして
    ログインしていることを必須にする。1(既定)のテナントは、これまで通りテナントAPIキー
    だけでも承認できる(後方互換)。TenantUserとしてログインしている場合は、1段階でも
    「誰が承認したか」がapprover_user_idに記録される。
    """
    session = await _get_owned_session(db, tenant, session_id)

    if session.status != "AWAITING_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"承認待ちの状態ではありません（現在の状態: {session.status}）",
        )

    if tenant.approval_stages > 1 and actor is None:
        raise HTTPException(
            status_code=403,
            detail="多段階承認が有効なテナントです。X-User-Tokenでログインしてから承認してください",
        )
    if actor is not None and actor.role not in ("approver", "owner"):
        raise HTTPException(status_code=403, detail="このユーザーには承認権限がありません")

    # 二度押し・古いボタンの再押下を弾く: pending状態の承認要求を先に確定させる
    pending = await db.execute(
        select(ApprovalModel).where(
            ApprovalModel.session_id == session_id,
            ApprovalModel.tenant_id == tenant.id,
            ApprovalModel.status == "PENDING",
        )
    )
    approval = pending.scalar_one_or_none()
    if approval is None:
        raise HTTPException(status_code=409, detail="この承認要求はすでに処理済みです")

    actor_label = str(actor.id) if actor is not None else str(tenant.id)
    approval.status = "APPROVED" if req.decision == "approve" else "REJECTED"
    approval.decided_by = actor_label
    approval.approver_user_id = actor.id if actor is not None else None
    approval.decided_at = datetime.utcnow()

    db.add(
        AuditLog(
            tenant_id=tenant.id,
            session_id=session_id,
            action="APPROVE" if req.decision == "approve" else "REJECT",
            actor=actor_label,
            comment=req.comment,
        )
    )

    # 却下、または最終段の承認でなければ、まだグラフを先に進めない
    # (次の段の承認待ちとしてPENDINGの承認要求を新たに積むだけ)
    if req.decision == "approve" and approval.stage < tenant.approval_stages:
        deadline = datetime.utcnow() + timedelta(hours=app_config.limits.approval_deadline_hours)
        db.add(
            ApprovalModel(
                tenant_id=tenant.id,
                session_id=session_id,
                payload=approval.payload,
                status="PENDING",
                stage=approval.stage + 1,
                deadline_at=deadline,
            )
        )
        await db.commit()
        return {
            "session_id": session.id,
            "status": "AWAITING_APPROVAL",
            "next_stage": approval.stage + 1,
            "approval_stages": tenant.approval_stages,
        }

    await db.commit()

    fire_and_forget(
        submit_approval_decision(
            tenant_id=str(tenant.id),
            session_id=str(session.id),
            decision=req.decision,
            comment=req.comment,
            app_config=app_config,
            llm=llm,
            checkpointer=checkpointer,
            embedding_provider=embedding_provider,
        ),
        tenant_id=str(tenant.id),
        session_id=str(session.id),
    )

    return {"session_id": session.id, "status": "RUNNING"}
