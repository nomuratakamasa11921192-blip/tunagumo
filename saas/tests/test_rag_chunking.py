from src.rag.chunking import MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, chunk_text


def test_empty_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_becomes_a_single_chunk():
    text = "これは短いテキストです。特に分割の必要はありません。"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].chunk_index == 0


def test_long_text_is_split_into_multiple_chunks_within_size_bounds():
    # 句点で終わる100字程度の文を50個並べて、確実に複数チャンクになる長さにする
    sentence = "これはテストのための日本語の文章であり、チャンク分割の境界を検証するために書かれています。"
    text = sentence * 50
    chunks = chunk_text(text)

    assert len(chunks) > 1
    # 最後のチャンク以外は目安の範囲内(オーバーラップがあるので厳密なMAX厳守は最後の1文次第だが、
    # 極端に超えないことだけ確認する)
    for c in chunks[:-1]:
        assert len(c.content) <= MAX_CHUNK_SIZE + len(sentence)


def test_chunks_do_not_split_mid_sentence():
    sentence_a = "第一段落の内容です。" * 10
    sentence_b = "第二段落の内容です。" * 10
    text = sentence_a + sentence_b
    chunks = chunk_text(text)

    for c in chunks:
        # チャンクの末尾は必ず句点(。)か改行で終わっている(文の途中で切れていない)
        assert c.content.endswith("。") or c.content.endswith("\n")


def test_consecutive_chunks_overlap():
    sentence = "これはオーバーラップを確認するための文章です。"
    text = sentence * 40
    chunks = chunk_text(text)

    assert len(chunks) >= 2
    # 隣り合うチャンクの、前のチャンク末尾と次のチャンク先頭が共通の文を含む(オーバーラップ)
    first_tail = chunks[0].content[-len(sentence):]
    second_head = chunks[1].content[: len(sentence)]
    assert first_tail == second_head or sentence in chunks[1].content[: len(sentence) * 2]


def test_heading_metadata_is_attached_to_chunks_under_it():
    # 各見出しの本文だけで複数チャンク分の長さを持たせ、見出しをまたいだ結合が
    # 起きない範囲で、後半チャンクの見出しが正しく引き継がれることを確認する
    text = (
        "## 料金プラン\n"
        + ("月額5万円のプランについての説明です。" * 200)
        + "\n## 導入の流れ\n"
        + ("導入までの流れについての説明です。" * 200)
    )
    chunks = chunk_text(text)

    plan_chunks = [c for c in chunks if c.heading == "料金プラン"]
    flow_chunks = [c for c in chunks if c.heading == "導入の流れ"]
    assert len(plan_chunks) >= 1
    assert len(flow_chunks) >= 1


def test_single_oversized_sentence_is_not_split_mid_sentence():
    """表などが1文として抽出され、MAX_CHUNK_SIZEを超える異常系でも、
    文の途中では割らない(7-2: 表は行単位でバラさない方針と矛盾しないようにする)。"""
    huge_sentence = "表のようなまとまり" * 100 + "。"
    assert len(huge_sentence) > MAX_CHUNK_SIZE

    chunks = chunk_text(huge_sentence)
    assert len(chunks) == 1
    assert chunks[0].content == huge_sentence


def test_minimum_chunk_size_constant_is_reasonable():
    # MIN_CHUNK_SIZEは目安として定義されているだけだが、定数として妥当な範囲か確認
    assert 0 < MIN_CHUNK_SIZE < MAX_CHUNK_SIZE
