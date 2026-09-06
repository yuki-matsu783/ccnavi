# ccnavi

Claude Code のツール呼び出しを hook で止め、止めた理由と代わりに取る手段を返す。

要求は [requirements.md](requirements.md)、設計は [ccnavi.md](ccnavi.md) にある。

## 開発

Go 1.27 以降。third-party の依存は持たない。

```sh
go build ./...           # 組み立て
go test -count=1 ./...   # テスト
go vet ./...             # 静的検査
golangci-lint run        # 追加の静的検査。設定は .golangci.yml
gofmt -l .               # 整形されていないファイルの一覧。出力が空なら通過
```

`-count=1` を付ける。受入テストは実行ファイルを外から叩くだけで内部パッケージを
import しないので、キャッシュが内部の変更に気づかず古い結果を返す。

手で 1 回動かす。

```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | go run . --rules testdata/rules.json
```

編集のたびに `.claude/hooks/lint-go.sh` が走り、整形・vet・lint をかけて
`ccnavi.exe` を作り直す。登録されている実行ファイルが常に今のソースになる。

## 設定

設定は環境変数で渡す。プロジェクトは `.claude/settings.json` の `env` ブロックに書く。
Claude Code の設定スキーマは独自のキーを受け付けないので、ここが唯一開いている場所になる。
hook には実行ファイルだけを登録すればよい。

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/ccnavi.exe\"", "timeout": 10 }
      ]}
    ]
  },
  "env": {
    "CCNAVI_MODE": "warn",
    "CCNAVI_RULES": ".claude/ccnavi/rules.json",
    "CCNAVI_LOG": ".claude/ccnavi/log.jsonl"
  }
}
```

| 変数 | 意味 |
|---|---|
| `CCNAVI_MODE` | `block`（既定）、`warn`、`off` |
| `CCNAVI_RULES` | ルールファイル。相対パスはプロジェクト根から |
| `CCNAVI_LOG` | 記録先。空文字にすると記録しない |

`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
`env` には相対パスを書く。値の変更はセッションを開き直すまで効かない。

プロジェクト根は `CLAUDE_PROJECT_DIR`、無ければ作業ディレクトリから親へ辿って
`.claude` を探して決める。どの階層から起動しても同じ設定に行き着く。

## ルール

1 件のルールは、当てるツール・探すもの・見つけたときに返す文面を 1 組で持つ。
文面を欠いたルールは受け付けない。

```json
{
  "id": "git-push",
  "match": "Bash",
  "pattern": "git push *",
  "message": "git push はエージェントからは実行しません。利用者に依頼してください。"
}
```

`pattern` の書き方は 3 つだけ。

| 書き方 | 意味 |
|---|---|
| `*` | 任意の文字列。空でもよい |
| `?` | 任意の 1 文字 |
| 空白 | 任意の空白の並び。`git push` は `git   push` にも当たる |

残りはそのままの文字として扱う。部分一致なので `git push` は
`/usr/bin/git push origin main` にも当たる。単語の切れ目は自動で見るので、
`sed *` は `sed` に当たり `sedate` には当たらない。`/` はどちらの区切り文字にも
当たるため、`secrets/` は Windows のパスでも効く。

`|` による択一のように上の 3 つで書けないものは、`pattern` の代わりに
`regex` に正規表現を書く。両方書いたルールは受け付けない。
正規表現は Go の RE2 なので、否定先読みと後方参照は使えない。

## 動作モード

| 値 | 挙動 |
|---|---|
| `block` | 判定し、該当すれば止める（ブロック）。指定が無いときはこれ |
| `warn` | 同じ判定を行い、止めずに「block なら止めていた」と伝える（警告） |
| `off` | 判定しない |

`off` だけは `.claude/settings.json` に書いても効かない。名指しで無視して理由を出す。
その設定ファイルは作業ツリーの中にあってエージェントが書き換えられ、変更は次の
ツール呼び出しから効く。監視される側が監視を止められる経路を残さないための扱い。
`off` にするときはセッションを起動する側の環境から渡す。

## 記録

判定した呼び出しは、通したものも含めて 1 行 1 件で追記される。

```
{"ts":"...","mode":"warn","event":"PreToolUse","tool":"Bash",
 "subject":"git push origin main","decision":"deny","enforced":false,
 "rules":["git-push"],"session":"...","ms":0.9}
```

`decision` は下した判定、`enforced` は実際に適用したか。`warn` は
`deny` かつ `enforced:false` になるので、1 つのファイルで「何を止めるはずだったか」と
「何を実際に止めたか」の両方が数えられる。

判定しなかった回は `decision` が `skip` になり、`reason` が付く。
`mode-off`、`event-not-checked`、`no-subject`、`payload-unusable`、
`rules-unreadable`、`deadline-exceeded` の 6 つ。
通した回も残すのは、記録が無いことを「ccnavi が動かなかった」と読めるようにするため。

## 構成

| 場所 | 中身 |
|---|---|
| `main.go` | 入口。期限を張って `cli.Run` を呼ぶだけ |
| `internal/hookio` | stdin の payload の解釈と、stdout に返す応答の組み立て |
| `internal/rules` | ルールファイルの読み込みと検証 |
| `internal/cli` | 引数と入力を 1 つの判定に繋ぐ |
| `main_test.go` | 受入テスト。内部の関数は呼ばず、標準入出力と終了コードだけを見る |
| `testdata/rules.json` | テスト用のルール |

## 配布物の条件

- 単一の実行ファイル。ランタイムの追加導入を要しない (`CGO_ENABLED=0`)
- 判定の間に外部プロセスを起こさず、ネットワークへ出ない
- 作業ディレクトリに依らず同じ入力に同じ判定を返す
