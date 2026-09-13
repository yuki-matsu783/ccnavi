# ADR-0005: 設定は `.claude/settings.json` の `env` に置く

状態: 採用

## 状況

設計の当初は `.claude/hooks/config.yaml` という独自の設定ファイルを想定していた。
実装の初期には `.claude/ccnavi/settings.json` という別ファイルにしたが、
settings.json が 2 つあるのが分かりにくいという指摘を受けた。

## 決定

設定は環境変数 `CCNAVI_*` で渡し、プロジェクトは `.claude/settings.json` の `env` ブロックに書く。
ccnavi 自身のソースツリーでだけ、`ccnavi.settings.local.json` で 1 人が上書きできる。

## 理由

Claude Code の `.claude/settings.json` にはスキーマ検証があり、`ccnavi` のような独自の
トップレベルキーを足す案は弾かれる。`env` ブロックが唯一開いている場所になる。
hook の登録も同じファイルにあるので、起動する実体と守る対象を 1 か所に寄せられる。

## 得たもの・失ったもの

- 得たもの: 設定ファイルが 1 つで済む。hook の `command` を `${CCNAVI_BIN_PATH}` で書けば、
  守る対象と起動する実体が黙って食い違わない
- 失ったもの: `env` の値はセッションを開き直すまで効かない（hook の `command` は即座に反映される）。
  モード名を変えたときに古い値が残って未知の値になり、既定に落ちて自分の作業が止まった。
  未知のモード値は報告するようにしてある
- 失ったもの: `${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
  `env` には相対パスを書く
- 失ったもの: `env` から `CCNAVI_BIN_PATH` の 1 行が消えると hook が起動しなくなる。
  パスを 1 か所にする利点と、その 1 行に全部が懸かる欠点の取り引き

## 採らなかった案

- `.claude/hooks/config.yaml`。ccnavi しか読まないファイルが 1 つ増える
- `.claude/ccnavi/settings.json`。settings.json が 2 つになる
- `.claude/settings.json` に独自キーを足す。スキーマ検証に弾かれる
