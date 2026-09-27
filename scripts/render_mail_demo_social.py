"""Render a local social cut from the verified September 27 demo recording.

No network access, publishing, or generated product UI. The reply card is
explicitly an excerpt from the verified email, not an inbox screen recording.
"""
import json
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / 'artifacts/mail-demo-20260927/review.mp4'
OUT = BASE / 'artifacts/mail-demo-20260927/social'

HEADER = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Meiryo,48,&H00F8F6F2,&H00F8F6F2,&H00241910,&H00241910,0,0,0,0,100,100,0,0,1,0,0,7,70,100,80,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''


def labels(name, rows):
    body = HEADER
    common = [(72, 130, 32, 'ツナグモ  /  不動産業務の操作デモ'),
              (72, 1690, 28, 'デモ用・架空の物件を使用')]
    for x, y, size, text in common + rows:
        body += (f'Dialogue: 0,0:00:00.00,0:01:00.00,Main,,0,0,0,,'
                 f'{{\\pos({x},{y})\\fs{size}}}{text}\n')
    (OUT / f'{name}.ass').write_text(body, encoding='utf-8-sig')


def main():
    if not SOURCE.is_file():
        raise SystemExit(f'Original recording missing: {SOURCE}')
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SystemExit('ffmpeg and ffprobe are required')
    OUT.mkdir(parents=True, exist_ok=True)

    def run(*args):
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', *args],
                       cwd=OUT, check=True)

    labels('01-hook', [
        (72, 340, 38, '設備の問い合わせ'),
        (72, 490, 74, '「宅配ボックス、\\Nありますか？」'),
        (72, 850, 57, '登録した物件資料から\\Nメールで一次回答。'),
        (72, 1240, 38, '実際の画面と、届いた返信を紹介'),
    ])
    labels('02-screen', [
        (72, 280, 38, '01  登録済み資料を確認'),
        (72, 370, 57, 'お客様への回答に使う\\N資料を選べます。'),
        (72, 1460, 38, '実画面の録画 ／ 通常速度'),
        (72, 1530, 30, '資料の内容・公開範囲は担当者が確認'),
    ])
    labels('03-reply', [
        (72, 280, 38, '02  実際に届いた返信'),
        (72, 390, 64, '資料にある設備を回答。'),
        (72, 620, 32, '受信したメール本文の抜粋'),
        (72, 730, 45, 'はい。デモ用の架空物件\\N「ツナグモハイツ203号室」には、\\N宅配ボックスと独立洗面台が\\Nあります。'),
        (72, 1130, 32, 'このメールはツナグモの自動応答です。'),
        (72, 1410, 29, '2026年9月27日の本人宛テストで受信確認'),
        (72, 1490, 29, '返信本文を読みやすく抜粋・改行した表示です。\\Nメール受信画面の録画ではありません。'),
    ])
    labels('04-end', [
        (72, 410, 70, 'よくある質問への\\N一次回答を支える。'),
        (72, 800, 44, '社外に伝えてよい資料を登録し、\\N問い合わせ対応に活用できます。'),
        (72, 1160, 38, 'ツナグモの機能を見る'),
        (72, 1270, 58, 'tunagumo.com'),
    ])

    encode = ['-c:v', 'libx264', '-preset', 'fast', '-crf', '19',
              '-pix_fmt', 'yuv420p', '-r', '30', '-an']
    for name, duration in [('01-hook', 4), ('03-reply', 10), ('04-end', 4)]:
        run('-f', 'lavfi', '-i', f'color=c=0x101924:s=1080x1920:r=30:d={duration}',
            '-vf', f'drawbox=x=72:y=218:w=120:h=6:color=0x45d3d0:t=fill,subtitles={name}.ass',
            *encode, f'{name}.mp4')
    run('-i', str(SOURCE), '-vf',
        'crop=800:640:240:42,scale=984:788,pad=1080:1920:48:600:0x101924,'
        'drawbox=x=72:y=218:w=120:h=6:color=0x45d3d0:t=fill,subtitles=02-screen.ass',
        *encode, '02-screen.mp4')
    (OUT / 'segments.ffconcat').write_text(''.join(
        f"file '{name}.mp4'\n" for name in ['01-hook', '02-screen', '03-reply', '04-end']),
        encoding='utf-8')
    target = 'ツナグモ_資料からメール回答_SNS縦型_確認用.mp4'
    run('-f', 'concat', '-safe', '0', '-i', 'segments.ffconcat',
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '96k', '-shortest', '-movflags', '+faststart', target)
    # Decode the entire deliverable and retain representative frames for review.
    run('-i', target, '-f', 'null', '-')
    for name, sec in [('hook', 1), ('screen', 12), ('reply', 20), ('end', 28)]:
        run('-ss', str(sec), '-i', target, '-frames:v', '1', f'check-{name}.png')
    metadata = subprocess.check_output([ffprobe, '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_name,width,height,r_frame_rate', '-of', 'json',
        str(OUT / target)], text=True)
    (OUT / 'verification.json').write_text(metadata, encoding='utf-8')
    print(json.dumps({'file': str(OUT / target), 'metadata': json.loads(metadata)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
