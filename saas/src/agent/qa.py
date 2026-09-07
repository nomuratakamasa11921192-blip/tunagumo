import unicodedata

from src.agent.schemas import FactCheckFinding, QualityFinding


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    # カタカナ -> ひらがな（全角カタカナの範囲のみ。長音符「ー」は対応がないため変換しない）
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


def check_forbidden(text: str, words: list[str]) -> list[str]:
    """全角半角・大文字小文字・カタカナひらがなを正規化して比較。ヒットした語のリストを返す。"""
    normalized_text = _normalize(text)
    return [word for word in words if _normalize(word) in normalized_text]


def filter_valid_quality_findings(findings: list[QualityFinding]) -> list[QualityFinding]:
    """evidence（原文引用）が空の指摘は、根拠なく判定している証拠として破棄する。"""
    return [f for f in findings if f.evidence.strip()]


def check_missing_citations(text: str, document_ids: list[str]) -> bool:
    """RAGで資料を取得した部署の成果物に、document_idの出典が1つも書かれていなければTrue。
    (7-5-5: 出典のない断定が混ざったらQAで差し戻す)"""
    return not any(doc_id in text for doc_id in document_ids)


def integrate_qa_verdict(
    *,
    forbidden_hits: dict[str, list[str]],
    fact_check: list[FactCheckFinding],
    quality_findings: list[QualityFinding],
    quality_fail_threshold: int,
    citation_document_ids: dict[str, list[str]] | None = None,
    board: dict[str, str] | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """機械チェック・事実チェック・品質チェックを統合し、(verdict, findings, warnings) を返す。

    - 機械チェック（禁止ワード）でヒット -> 即FAIL
    - RAGで資料を取得したのに出典(document_id)が本文にない -> 即FAIL(7-5-5)
    - 事実チェックでFAIL -> FAIL
    - 品質チェックはevidence必須のfindingのみ有効。有効なFAILが閾値以上 -> FAIL
      閾値未満（1件など）なら警告として記録し、通過させる
    """
    findings: list[dict] = []
    warnings: list[dict] = []

    machine_failed = False
    for dept_id, words in forbidden_hits.items():
        if words:
            machine_failed = True
            findings.append(
                {
                    "stage": "MACHINE",
                    "dept_id": dept_id,
                    "issue": f"禁止ワードを検出: {', '.join(words)}",
                }
            )

    board = board or {}
    for dept_id, document_ids in (citation_document_ids or {}).items():
        if not document_ids:
            continue
        text = board.get(dept_id, "")
        if check_missing_citations(text, document_ids):
            machine_failed = True
            findings.append(
                {
                    "stage": "MACHINE",
                    "dept_id": dept_id,
                    "issue": f"社内資料検索(RAG)で取得した資料の出典が本文にありません: {document_ids}",
                }
            )

    fact_failed = False
    for fc in fact_check:
        if fc.verdict == "FAIL":
            fact_failed = True
            findings.append(
                {
                    "stage": "FACT_CHECK",
                    "dept_id": fc.dept_id,
                    "issue": fc.issue,
                    "evidence": fc.evidence,
                }
            )

    valid_quality = filter_valid_quality_findings(quality_findings)
    quality_fails = [f for f in valid_quality if f.verdict == "FAIL"]

    quality_failed = len(quality_fails) >= quality_fail_threshold

    if quality_failed:
        for f in quality_fails:
            findings.append(
                {
                    "stage": "QUALITY",
                    "criterion": f.criterion,
                    "evidence": f.evidence,
                    "reason": f.reason,
                }
            )
    else:
        for f in quality_fails:
            warnings.append(
                {
                    "stage": "QUALITY",
                    "criterion": f.criterion,
                    "evidence": f.evidence,
                    "reason": f.reason,
                }
            )

    if machine_failed or fact_failed or quality_failed:
        return "FAIL", findings, warnings

    return "PASS", findings, warnings
