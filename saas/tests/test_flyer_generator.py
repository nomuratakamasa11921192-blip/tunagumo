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
