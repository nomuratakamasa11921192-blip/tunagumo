from src.agent.qa import check_forbidden, check_missing_citations, integrate_qa_verdict
from src.agent.schemas import FactCheckFinding, QualityFinding


def test_check_forbidden_normalizes_width_and_case():
    hits = check_forbidden("これは業界最安値です", ["業界最安値"])
    assert hits == ["業界最安値"]


def test_check_forbidden_normalizes_katakana_hiragana():
    hits = check_forbidden("なんばーわんの実績です", ["ナンバーワン"])
    assert hits == ["ナンバーワン"]


def test_check_forbidden_no_hit():
    assert check_forbidden("通常の文章です", ["業界最安値", "絶対"]) == []


def test_evidence_required_findings_without_evidence_are_discarded():
    findings = [
        QualityFinding(criterion="SPECIFICITY", verdict="FAIL", evidence="", reason="根拠なし"),
        QualityFinding(criterion="ACTIONABILITY", verdict="FAIL", evidence="「SNSを活用する」", reason="具体性なし"),
    ]
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[],
        quality_findings=findings,
        quality_fail_threshold=2,
    )
    # evidence が空の指摘は破棄されるので、有効なFAILは1件だけ -> 閾値2未満で警告として通過
    assert verdict == "PASS"
    assert len(warnings) == 1


def test_two_or_more_quality_fails_causes_fail():
    findings = [
        QualityFinding(criterion="SPECIFICITY", verdict="FAIL", evidence="引用1", reason="a"),
        QualityFinding(criterion="ACTIONABILITY", verdict="FAIL", evidence="引用2", reason="b"),
    ]
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[],
        quality_findings=findings,
        quality_fail_threshold=2,
    )
    assert verdict == "FAIL"
    assert len(results) == 2


def test_forbidden_word_hit_forces_fail_regardless_of_others():
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={"copy_dept": ["絶対"]},
        fact_check=[],
        quality_findings=[],
        quality_fail_threshold=2,
    )
    assert verdict == "FAIL"


def test_fact_check_fail_causes_overall_fail():
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[FactCheckFinding(dept_id="planning_dept", verdict="FAIL", issue="架空の実績", evidence="売上10億円")],
        quality_findings=[],
        quality_fail_threshold=2,
    )
    assert verdict == "FAIL"


def test_check_missing_citations_true_when_no_document_id_appears():
    assert check_missing_citations("特別休暇についての説明です。", ["doc-123", "doc-456"]) is True


def test_check_missing_citations_false_when_any_document_id_appears():
    text = "特別休暇について(出典: doc-123)説明します。"
    assert check_missing_citations(text, ["doc-123", "doc-456"]) is False


def test_rag_citation_missing_forces_fail_even_with_otherwise_clean_output():
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[],
        quality_findings=[],
        quality_fail_threshold=2,
        citation_document_ids={"planning_dept": ["doc-123"]},
        board={"planning_dept": "特別休暇は年5日付与されます。"},
    )
    assert verdict == "FAIL"
    assert any("出典" in r["issue"] for r in results)


def test_rag_citation_present_does_not_force_fail():
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[],
        quality_findings=[],
        quality_fail_threshold=2,
        citation_document_ids={"planning_dept": ["doc-123"]},
        board={"planning_dept": "特別休暇は年5日付与されます(出典: doc-123)。"},
    )
    assert verdict == "PASS"


def test_dept_with_no_retrieved_documents_is_not_penalized():
    """RAGを使わなかった(または検索結果が0件だった)部署は、citation_document_idsに
    キー自体が無いか空リストになるはずで、その場合は出典チェックの対象外になる。"""
    verdict, results, warnings = integrate_qa_verdict(
        forbidden_hits={},
        fact_check=[],
        quality_findings=[],
        quality_fail_threshold=2,
        citation_document_ids={"planning_dept": []},
        board={"planning_dept": "通常の成果物です。"},
    )
    assert verdict == "PASS"
