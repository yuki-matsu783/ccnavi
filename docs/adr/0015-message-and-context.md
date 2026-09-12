# ADR-0015: `message` は `deny` だけの欄にし、モデルへの一言は `additionalContext` に分ける

状態: 採用

## 状況

ルールの `message` は「なぜ止めたか、代わりに何をするか」を止められたモデルに伝える文で、
`permissionDecisionReason` として返る。ask と allow に書いた文面がどこに届くかは
公式文書の読み方が割れていたので、Claude Code 2.1.235 で実測した。

- ask の文面は人の確認ダイアログに出るが、Yes を押した後もモデルには届かない。No のときは
  拒否の定型文だけがモデルに届いてターンが終わる
- allow の文面はどこにも出ない
- `additionalContext` は deny / ask / allow のどれでもモデルに届く。deny と ask では
  `permissionDecisionReason` と一緒に届き、ask では Yes のときだけ届く
- `/compact` の直後に `source: compact` の SessionStart が同じ session_id で届く

## 決定

`message` は `deny` だけの欄（必須）。`ask` と `allow` に書くと `--lint` が error にする。
モデルに伝えたいことはタイプによらず `additionalContext`（毎回）と `additionalContextOnce`
（文脈ごとに 1 度）に書き、本文をファイルで渡す `additionalContextFile` / `additionalContextOnceFile`
も持つ。once の記憶はセッションの開始（起動・再開・compact の後）で捨てる。dry-run でも届ける。

## 理由

書いた人は「モデルに届く」と思って書くので、届かない欄を残さない。「通すが、これを踏まえて
進めろ」を言う手段が無いと、それを言いたい場所を deny にして 1 往復させるか、CLAUDE.md に
書いて全体に効かせるかしかなくなる（REQ-PRE-11）。dry-run でも届けるのは、`enable` に
切り替えて初めて読まれる文を残さないため。once を compact で忘れるのは、文脈が新しくなるたびに
1 度渡すという意味を保つため。

広い `allow` に毎回の文を書くと、当たった回ごとに同じ文が積まれて 2 回目から読まれなくなる。
`--lint` は何にでも当たる `allow` と選択肢が 3 つ以上ある `regex` に書いた毎回の文を warn にする。

ファイルの本文は先頭 4000 文字で切り、切ったことを末尾に添える。黙って切ると、モデルは途中で
終わる文を全部だと思って読む。絶対パスと `..` で上に出るパスは読まない。ルールから任意の
ファイルをモデルに流し込める形にはしない。

## 得たもの・失ったもの

- 得たもの: 止めずに案内する道ができた。長い説明を 1 度だけ読ませられる
- 失ったもの: 控えの置き場が無い（`--state ""`）と once は毎回届く。覚えられないなら黙るのでは
  なく言う側に倒す

## 採らなかった案

- `message` を全タイプに残す。届かない欄が残る
- 案内を CLAUDE.md に書く。全体に効いてしまい、当たった場所でだけ言えない
