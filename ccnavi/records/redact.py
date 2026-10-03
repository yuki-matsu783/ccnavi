"""記録に残す文字列から、秘密の形をした部分を伏せる。

判定には使わない。止める・通すは伏せる前の文字列で決め、伏せるのは `logs/decisions.jsonl` に
書く 1 行だけ（audit）。判定の側で伏せると、伏せた形にルールを当てることになり、
`curl -H 'Authorization: …' …` のような呼び出しの読みが変わる。

記録にはコマンドの全文が入る。`curl -H 'Authorization: Bearer …'`、`mysql -p<パスワード>`、
`git clone https://oauth2:<トークン>@…` を 1 回打てば、その値が平文でディスクに残り、
ローテートした記録（prune）と一緒に何日も残る。

伏せ方は長さで 2 通り。短い値（SHORT 字未満）は全部を `***` にする。長い値は頭の
KEEP_HEAD 字と尻の KEEP_TAIL 字を残し、あいだを `***` にする。残すのは、同じ値が繰り返し
出ているか、どの種類のトークンかを記録から見分けられるようにするため。短い値で残すと、
残した字だけで値の大半が読める。

見つけ方は形だけ。値そのものが秘密かどうかは分からないので、取りこぼしも、
秘密でないものを伏せることもある。後者は記録が少し読みにくくなるだけで、判定は変わらない。
`$VAR` や `${VAR}` のように変数を指すだけの値は伏せない（値そのものはコマンドに無い）。
"""

from __future__ import annotations

import re

# これより短い値は全部を伏せる。
SHORT = 18
# 長い値で残す頭と尻の字数。
KEEP_HEAD = 6
KEEP_TAIL = 4
MASK = "***"

# 引用の外で、値の終わりとして読む字。`\x00` は unwrapped がコマンドをつなぐ目印。
_BARE_VALUE = r"[^\s'\"&;|,)}<>`\x00]+"
# 値。引用されていれば閉じ引用まで、されていなければ区切りの字の手前まで。
_VALUE = r"(?:'(?P<sq>[^'\n]*)'|\"(?P<dq>[^\"\n]*)\"|(?P<bare>" + _BARE_VALUE + r"))"

# 名前が秘密を指す `名前=値` / `名前: 値`。名前はこれで終わるもの（`GITHUB_TOKEN`、
# `db_password`、`x-api-key`、`aws_secret_access_key`、`SECRET_KEY`、`MYSQL_PWD`、`_authToken`、
# `SESSION_COOKIE`）。途中に含むだけの名前（`tokenizer`、`secretName`）は読まない。
# `pwd` は前に `_` か `-` が付いた形だけ。素の `PWD` はいまの場所で、秘密ではない。
_KEY_WORDS = (
    r"token|password|passwd|[_-]pwd|secret|secret[_-]?key|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|auth[_-]?token|credentials?|cookie"
)
# 名前の頭は長さを限り、名前の途中から始めない（前の字が名前の字なら当てない）。どちらかが
# 無いと、`-` や `_` が続くだけの文字列で、始まりの位置ごとに末尾まで読み進めては戻る形になり、
# 手間が長さの 2 乗で増える（1.5 万字で 10 秒）。記録は実行前の判定の期限の中で書くので、
# 期限を過ぎると hook の拒否ごと捨てられる。これより長い名前は取りこぼす。
_NAME_HEAD = r"[A-Za-z0-9_.-]{0,64}"
_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_.-])" + _NAME_HEAD + r"(?:" + _KEY_WORDS + r")[\"']?\s*[=:]\s*" + _VALUE,
    re.IGNORECASE,
)
# 空白で値を渡すフラグ（`--password xxx`、`--token xxx`）。`=` の形は _ASSIGN が読む。
_FLAG = re.compile(
    r"(?<![A-Za-z0-9-])--(?:password|passwd|token|secret|api-key|apikey|access-token|auth-token)"
    r"\s+" + _VALUE,
    re.IGNORECASE,
)
# HTTP の Authorization ヘッダの値。方式の語（Bearer / Basic / token など）は残す。
_AUTH_HEADER = re.compile(
    r"\b(?:proxy-)?authorization\s*:\s*(?:[A-Za-z]+\s+)?(?P<bare>" + _BARE_VALUE + r")",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bbearer\s+(?P<bare>[A-Za-z0-9._~+/=-]+)", re.IGNORECASE)
# URL に埋めた資格情報（`https://user:<値>@host`）。値に `@` が入ることがあるので、ホストの
# 手前の最後の `@` までを伏せる（ccnavi-common.sh の ccnavi_mask_url と同じ読み）。空白と
# `/` はまたがない。
_URL_USERINFO = re.compile(r"://[^/\s:@'\"\x00]+:(?P<bare>[^\s/'\"\x00]+)@")
# HTTP の Cookie ヘッダの値。`;` で並ぶ値を全部、引用か行の終わりまで伏せる。
_COOKIE_HEADER = re.compile(
    r"\b(?:set-)?cookie\s*:\s*(?P<bare>[^'\"\n\x00]+)",
    re.IGNORECASE,
)
# 形そのものがトークンだと言えるもの。
_TOKENS = re.compile(
    r"(?<![A-Za-z0-9])(?P<bare>"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}"  # AWS のアクセスキー ID
    r"|gh[pousr]_[A-Za-z0-9]{20,}"  # GitHub の各種トークン
    r"|github_pat_[A-Za-z0-9_]{20,}"  # GitHub の fine-grained トークン
    r"|glpat-[A-Za-z0-9_-]{20,}"  # GitLab の個人アクセストークン
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"  # Slack のトークン
    r"|sk-[A-Za-z0-9_-]{20,}"  # OpenAI / Anthropic の API キー（`sk-ant-…`、`sk-proj-…` を含む）
    r"|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"  # Stripe の秘密鍵と制限付き鍵
    r"|AIza[0-9A-Za-z_-]{30,}"  # Google の API キー
    r"|npm_[A-Za-z0-9]{30,}"  # npm のアクセストークン
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r")(?![A-Za-z0-9])"
)
# 秘密鍵の本文。見出しと結びの行は残し、あいだを伏せる。
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?P<bare>.*?)(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    re.DOTALL,
)

# コマンドを限って読むフラグ。`-p` や `-u` はほかのコマンドでは別の意味なので
# （`mkdir -p`、`ssh -p 22`、`sort -u`、`ls -a`）、そのコマンドの中でだけ読む。
# コマンドの範囲は、名前から次のコマンドの切れ目（`;` `|` `&` 改行 `\x00`）の手前まで。
_SEGMENT_REST = r"[^\n;|&\x00]*"
_SEGMENTS = (
    # mysql 系の `-p<パスワード>`（空白を挟まない形だけがパスワードになる）。
    (
        re.compile(r"\b(?:mysql\w*|mariadb\w*)\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)-p" + _VALUE),
    ),
    # curl の `-u user:<パスワード>` / `--user user:<パスワード>`。
    (
        re.compile(r"\bcurl\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)(?:-u|--user)[\s=]*['\"]?[^\s:'\"\x00]+:(?P<bare>[^\s'\"\x00]+)"),
    ),
    # curl の `-b <クッキー>` / `--cookie <クッキー>`。
    (
        re.compile(r"\bcurl\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)(?:-b|--cookie)[\s=]+" + _VALUE),
    ),
    # sshpass の `-p <パスワード>` / `-p<パスワード>`。見るのは sshpass 自身のオプションだけで、
    # 後ろに続くコマンド（`ssh -p 22`）の `-p` は読まない。
    (
        re.compile(r"\bsshpass\b" + _SEGMENT_REST),
        re.compile(r"^sshpass(?:\s+-[^p\s]\S*)*\s+-p\s*" + _VALUE),
    ),
    # docker login の `-p <パスワード>`（`--password` は _FLAG が読む）。
    (
        re.compile(r"\bdocker\s+login\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)-p\s*" + _VALUE),
    ),
    # redis-cli の `-a <パスワード>` / `--pass <パスワード>`。
    (
        re.compile(r"\bredis-cli\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)(?:-a|--pass)\s+" + _VALUE),
    ),
    # `aws configure set aws_secret_access_key <値>`（名前と値を空白で分ける形）。
    (
        re.compile(r"\baws\s+configure\b" + _SEGMENT_REST),
        re.compile(r"(?<=\s)(?:aws_secret_access_key|aws_session_token)\s+" + _VALUE),
    ),
)


def redact(text: str) -> str:
    """秘密の形をした部分を伏せた文字列を返す。見つからなければそのまま返す。"""
    if not text:
        return text
    spans = _spans(text)
    if not spans:
        return text
    out = []
    last = 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(mask(text[start:end]))
        last = end
    out.append(text[last:])
    return "".join(out)


def mask(value: str) -> str:
    """値 1 つを伏せる。短ければ全部、長ければ頭と尻を残す。"""
    if len(value) < SHORT:
        return MASK
    return value[:KEEP_HEAD] + MASK + value[-KEEP_TAIL:]


def _spans(text: str) -> list[tuple[int, int]]:
    """伏せる範囲を、重なりをまとめて前から並べる。

    同じ値に 2 つの形が当たる（`GITHUB_TOKEN=ghp_…` は名前でもトークンの形でも当たる）ので、
    範囲を先に集めてから 1 度だけ伏せる。形ごとに順に置き換えると、伏せた後の文字列を
    次の形がもう 1 度伏せ、残した頭と尻まで消える。
    """
    found: list[tuple[int, int]] = []
    for pattern in (
        _PRIVATE_KEY,
        _TOKENS,
        _AUTH_HEADER,
        _BEARER,
        _URL_USERINFO,
        _COOKIE_HEADER,
        _ASSIGN,
        _FLAG,
    ):
        found.extend(_value_spans(pattern, text, 0))
    for segment, option in _SEGMENTS:
        for part in segment.finditer(text):
            found.extend(_value_spans(option, part.group(0), part.start()))
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _value_spans(pattern: re.Pattern[str], text: str, offset: int) -> list[tuple[int, int]]:
    """当たった値の範囲。変数を指すだけの値と空の値は外す。"""
    out = []
    for m in pattern.finditer(text):
        for name in ("sq", "dq", "bare"):
            if name in pattern.groupindex and m.group(name) is not None:
                start, end = m.span(name)
                break
        else:
            continue
        value = text[start:end]
        if not value.strip() or value.startswith("$"):
            continue
        out.append((start + offset, end + offset))
    return out
