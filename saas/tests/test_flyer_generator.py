from src.core.flyer_generator import FlyerData, generate_flyer_pdf


def test_generate_flyer_pdf_produces_pdf_bytes():
    data = FlyerData(
        title="テスト物件のご案内",
        body_text="所在地: 東京都〇〇区\n賃料: 10万円\n特徴: 駅徒歩5分",
        image_urls=[],
    )

    pdf_bytes = generate_flyer_pdf(data)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500


def test_generate_flyer_pdf_escapes_html_in_body_text():
    data = FlyerData(title="<script>alert(1)</script>", body_text="<b>not bold</b>", image_urls=[])

    pdf_bytes = generate_flyer_pdf(data)

    assert pdf_bytes.startswith(b"%PDF")


def test_generate_flyer_pdf_embeds_generated_image(tmp_path, monkeypatch):
    # 生成画像の相対URL(/api/generated-images/...)がPDFに実際に埋め込まれること。
    # 以前はflexレイアウトのせいで画像が描画されず、PDFに載っていなかった。
    import io

    from PIL import Image

    from src.core import openai_image_client

    monkeypatch.setattr(openai_image_client, "GENERATED_IMAGES_DIR", tmp_path)
    name = "9" * 32 + ".jpg"
    buf = io.BytesIO()
    Image.effect_noise((400, 300), 64).convert("RGB").save(buf, "JPEG", quality=95)
    (tmp_path / name).write_bytes(buf.getvalue())

    without_image = generate_flyer_pdf(FlyerData(title="t", body_text="b", image_urls=[]))
    with_image = generate_flyer_pdf(
        FlyerData(title="t", body_text="b", image_urls=[f"/api/generated-images/{name}", "file:///etc/passwd"])
    )

    assert len(with_image) - len(without_image) > len(buf.getvalue()) // 2


def test_flyer_formats_markdown_without_activating_customer_html():
    from src.core.flyer_generator import _render_html
    rendered = _render_html(FlyerData(
        title="架空物件", body_text="## 設備\n- 宅配ボックス\n- **独立洗面台**\n\n<script>alert(1)</script>"
    ))
    assert "<h2>設備</h2>" in rendered
    assert "## 設備" not in rendered
    assert "<li>宅配ボックス</li>" in rendered
    assert "<strong>独立洗面台</strong>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
