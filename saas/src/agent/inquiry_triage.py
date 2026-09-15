"""社外からの問い合わせ(入居者・見込み客)の緊急度判定と分類(2026-09-15)。

24時間の一次受けで最も大事なのは「緊急の連絡を取りこぼさないこと」。そのためAIの判断に
頼らず、キーワードで確実に判定する(public_responderのエスカレーション判定と同じ考え方:
迷ったら人に回す)。ここで決めた定型の安全案内は内容が確定しているので自動で返信してよく、
それ以外の「分からない」問い合わせは担当者へ回す(ユーザー方針: 確定しているものだけ自動送信)。

安全案内の文面は、消防・ガス事業者等が一般に案内している初期対応の範囲に留める。
「絶対」「必ず」等の断定表現は使わない(SYSTEM_PROMPT.md §0.1)。
"""

from dataclasses import dataclass

URGENT = "urgent"
NORMAL = "normal"


@dataclass(frozen=True)
class EmergencyRule:
    kind: str
    label: str
    keywords: tuple[str, ...]
    guidance: str


EMERGENCY_RULES: tuple[EmergencyRule, ...] = (
    EmergencyRule(
        kind="fire",
        label="火災・煙",
        keywords=("火事", "火災", "煙が", "燃えて"),
        guidance="火災の恐れがある場合は、ただちに安全な場所へ避難し、119番へ通報してください。",
    ),
    EmergencyRule(
        kind="medical",
        label="急病・けが",
        keywords=("意識がない", "倒れて", "救急車", "大けが", "息をしていない"),
        guidance="急病やけがの場合は、ためらわず119番へ通報してください。",
    ),
    EmergencyRule(
        kind="crime",
        label="防犯・身の危険",
        keywords=("不審者", "侵入", "泥棒", "空き巣", "暴力", "襲われ"),
        guidance="身の危険を感じる場合は、安全な場所へ移動し、110番へ通報してください。",
    ),
    EmergencyRule(
        kind="gas",
        label="ガス",
        keywords=("ガス臭", "ガスの臭い", "ガスのにおい", "ガス漏れ"),
        guidance=(
            "ガスの臭いがする場合は、火を使わず、電気のスイッチや換気扇に触れないようにして窓を開け、"
            "屋外に出てからガス会社の緊急連絡先へご連絡ください。"
        ),
    ),
    EmergencyRule(
        kind="electric",
        label="漏電・焦げ臭さ",
        keywords=("漏電", "焦げ臭", "火花", "感電"),
        guidance="漏電や焦げ臭さがある場合は、安全に操作できればブレーカーを落とし、コンセントや家電に触れないでください。",
    ),
    EmergencyRule(
        kind="water",
        label="水漏れ",
        keywords=("水漏れ", "漏水", "水が止まらない", "雨漏り", "水浸し", "水があふれ", "水が溢れ"),
        guidance="水漏れの場合は、可能であれば止水栓または水道の元栓を閉めてください。下の階へ漏れている恐れがある場合もお知らせください。",
    ),
    EmergencyRule(
        kind="trapped",
        label="閉じ込め",
        keywords=("閉じ込められ", "エレベーターが止ま"),
        guidance="エレベーターに閉じ込められた場合は、かご内の非常ボタンで保守会社へ連絡し、無理に扉を開けないでください。",
    ),
    EmergencyRule(
        kind="lockout",
        label="鍵の紛失・閉め出し",
        keywords=("鍵をなくし", "鍵を無くし", "鍵を紛失", "鍵を落とし", "閉め出され", "部屋に入れない", "家に入れない"),
        guidance="鍵の紛失・閉め出しについては、担当者が確認してご連絡いたします。",
    ),
)

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("設備の故障", ("故障", "壊れ", "動かない", "使えない", "エアコン", "給湯器", "お湯が出ない", "トイレ", "詰まり", "換気扇", "インターホン", "電気がつかない")),
    ("騒音・近隣", ("騒音", "うるさい", "隣の部屋", "上の階", "下の階", "近隣", "ゴミ出し", "ペット")),
    ("家賃・契約", ("家賃", "契約", "更新", "解約", "退去", "敷金", "礼金", "支払い", "振込", "引き落とし")),
    ("内見・物件", ("内見", "空室", "空き部屋", "物件", "見学", "申し込み", "入居")),
)
EMERGENCY_CATEGORY = "緊急"
OTHER_CATEGORY = "その他"


@dataclass(frozen=True)
class Triage:
    urgency: str
    category: str
    emergency: EmergencyRule | None = None


def triage(message: str) -> Triage:
    """メッセージから緊急度と分類を決める。緊急キーワードは分類より優先する。"""
    for rule in EMERGENCY_RULES:
        if any(kw in message for kw in rule.keywords):
            return Triage(urgency=URGENT, category=f"{EMERGENCY_CATEGORY}({rule.label})", emergency=rule)
    for category, keywords in CATEGORY_RULES:
        if any(kw in message for kw in keywords):
            return Triage(urgency=NORMAL, category=category)
    return Triage(urgency=NORMAL, category=OTHER_CATEGORY)


def emergency_reply(rule: EmergencyRule, *, emergency_phone: str | None, escalation_message: str) -> str:
    """緊急時に自動で返す定型文(安全案内 + 緊急連絡先 + 担当者から折り返す旨)。"""
    parts = [rule.guidance]
    if emergency_phone:
        parts.append(f"お急ぎの場合は、緊急連絡先({emergency_phone})へお電話ください。")
    parts.append(escalation_message)
    return "\n".join(parts)
