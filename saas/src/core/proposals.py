"""物件提案・追客メールの中身を作る部品(2026-09-16)。照合(条件に合う物件を選ぶ)と本文の組み立ては
AIを使わない決まったルールで行う。物件の事実(価格・所在地・間取り等)はDBの値だけを使うので、
内容が確定しており、テナントが自動送信を有効にしている場合は承認なしで送ってよい
(ユーザー方針: 確定しているものだけ自動送信)。

特定電子メール法への対応:
- 事前に同意を得た見込み客(Lead.consent_at)にだけ送る
- 送信者の名称・住所・問い合わせ先と、配信停止の方法を必ず表示する(compose_footer)。
  本文(Proposal.body)とは別に送信時に付けるので、編集で消されることはない
- 配信停止はワンクリックで受け付ける(List-Unsubscribe / List-Unsubscribe-Postヘッダも付ける)
"""

from src.core.config import settings
from src.core.models import Lead, Property, Tenant

MAX_PROPERTIES_PER_PROPOSAL = 5


def is_match(prop: Property, lead: Lead) -> bool:
    if prop.status != "available":
        return False
    if lead.deal_type and prop.deal_type != lead.deal_type:
        return False
    if lead.areas and not any(a and (a in prop.location or a in prop.station) for a in lead.areas):
        return False
    if lead.max_price_yen is not None and prop.price_yen > lead.max_price_yen:
        return False
    if lead.layouts and prop.layout.upper() not in {layout.upper() for layout in lead.layouts}:
        return False
    if lead.max_walk_minutes is not None and (prop.walk_minutes is None or prop.walk_minutes > lead.max_walk_minutes):
        return False
    if lead.min_floor_area_sqm is not None and (prop.floor_area_sqm is None or prop.floor_area_sqm < lead.min_floor_area_sqm):
        return False
    return True


def format_price(prop: Property) -> str:
    man = prop.price_yen / 10_000
    text = f"{man:,.1f}".rstrip("0").rstrip(".") + "万円"
    return f"賃料 {text}/月" if prop.deal_type == "rent" else f"価格 {text}"


def format_property(prop: Property) -> str:
    lines = [f"■ {prop.name}"]
    place = " / ".join(
        p for p in (
            f"所在地: {prop.location}" if prop.location else "",
            f"最寄り駅: {prop.station}" + (f" 徒歩{prop.walk_minutes}分" if prop.walk_minutes is not None else "") if prop.station else "",
        ) if p
    )
    if place:
        lines.append(f"  {place}")
    detail = " / ".join(
        p for p in (
            format_price(prop),
            f"間取り: {prop.layout}" if prop.layout else "",
            f"面積: {prop.floor_area_sqm:g}㎡" if prop.floor_area_sqm is not None else "",
            f"{prop.built_year}年築" if prop.built_year else "",
        ) if p
    )
    lines.append(f"  {detail}")
    if prop.features:
        lines.append(f"  {prop.features[:200]}")
    if prop.url:
        lines.append(f"  詳細: {prop.url}")
    return "\n".join(lines)


def build_proposal(tenant: Tenant, lead: Lead, properties: list[Property]) -> tuple[str, str]:
    sender = tenant.marketing_sender_name or tenant.name
    subject = f"【{sender}】ご希望の条件に合う物件のご案内({len(properties)}件)"
    body = (
        f"{lead.name}様\n\n"
        f"{sender}です。ご希望の条件に合う物件が見つかりましたので、ご案内いたします。\n\n"
        + "\n\n".join(format_property(p) for p in properties)
        + "\n\n気になる物件がございましたら、このメールにご返信ください。内見のご予約も承ります。\n"
        "※掲載内容は作成時点の情報です。最新の状況はお問い合わせください。"
    )
    return subject, body


def build_follow_up(tenant: Tenant, lead: Lead) -> tuple[str, str]:
    sender = tenant.marketing_sender_name or tenant.name
    subject = f"【{sender}】お部屋探しの状況はいかがでしょうか"
    body = (
        f"{lead.name}様\n\n"
        f"{sender}です。先日は物件のご案内をお送りいたしました。その後、お部屋探しの状況はいかがでしょうか。\n\n"
        "ご希望の条件に変更がございましたら、このメールにご返信いただければ、条件に合わせて改めてご案内いたします。"
        "ご不明な点も、お気軽にお問い合わせください。"
    )
    return subject, body


def sender_info_missing(tenant: Tenant) -> list[str]:
    missing = []
    if not tenant.marketing_sender_name:
        missing.append("送信者名(会社名)")
    if not tenant.marketing_sender_address:
        missing.append("住所")
    if not tenant.marketing_sender_contact:
        missing.append("問い合わせ先")
    return missing


def unsubscribe_url(lead: Lead) -> str:
    base = settings.saas_public_url.rstrip("/") if settings.saas_public_url else ""
    return f"{base}/api/unsubscribe/{lead.unsubscribe_token}"


def compose_footer(tenant: Tenant, lead: Lead) -> str:
    return (
        "\n\n――――――――――\n"
        f"{tenant.marketing_sender_name}\n"
        f"{tenant.marketing_sender_address}\n"
        f"お問い合わせ: {tenant.marketing_sender_contact}\n"
        f"今後このようなご案内が不要な場合は、こちらから配信を停止できます:\n{unsubscribe_url(lead)}"
    )


def unsubscribe_headers(lead: Lead) -> dict:
    return {
        "List-Unsubscribe": f"<{unsubscribe_url(lead)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
