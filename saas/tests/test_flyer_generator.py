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
