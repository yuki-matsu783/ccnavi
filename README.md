# ccnavi

ccnavi は、Claude Code を使った開発を、ルール・チケット・必要時の情報参照・振り返りで支えるツールです。
Claude Code の hook でツール呼び出しを止め、止めた理由と代わりに取る手段を返す、というガードが出発点です。
いまは、止めるだけでなく、作業を合意からレビューまで進め、必要な資料を必要なときに引き、記録から次の作業の決まりを良くするところまでを受け持ちます。

## コア機能

| # | コア機能 | 何をするか | 主な入口 |
|---|---|---|---|
| 1 | ルールによる作業のガードとガイド | ツール実行とファイル操作をルールに照らして判定し、止める・人間に確認する・注意点や進め方を伝える。拒否には理由と代わりの手段を添える。ルールが関係する場面で、理由・注意点・手順を届ける | [ルール](docs/41_guide/rules.md)、[ツール実行後チェック](docs/41_guide/hooks.md) |
| 2 | チケットによる作業・レビュー管理 | 作業の目的・範囲・計画をチケットで管理し、人間の承認、フェーズの進行、先行との依存、子への分担、レビュー、指摘への対応を経て、合意した作業をレビューまで進める。ワークツリーとサブエージェントの連携、実績の差分に基づくリスク評価、計画テンプレートの利用を含む | [チケット](docs/41_guide/tickets.md)、[レビュー](docs/41_guide/review.md) |
| 3 | ドキュメント・スキルの検索と必要時の参照 | 文書とスキルの本文を最初から全部は渡さず、目録と索引で読む対象を絞り、本文はエージェントが必要になったときに読む。ccnavi 自身のヘルプに限らず、ワークスペースとプロジェクトの文書・作業手順を引く | [ドキュメントの索引](docs/41_guide/docs-search.md)、[プロジェクトのスキル](docs/41_guide/tickets.md) |
| 4 | 記録と振り返りによるルール・作業手順の改善 | 判定の記録、作業・レビューの気づき、振り返りから、ルール・作業手順・スキル・文書・計画テンプレートの改善候補を見つけて提案し、人間が採ったものを次の作業に活かす | [記録と振り返り](docs/41_guide/records.md)、[設定の確かめ方](docs/41_guide/check.md) |

### 1 つ目と 3 つ目の違い

どちらも「必要な情報をモデルへ届ける」仕組みだが、届くきっかけが違う。

- ガードとガイド（1）: 操作や作業の状況がきっかけになる。判定は ccnavi の hook 処理が、モデルのコンテキストの外で行う。
  モデルがルールを読んで自発的に守ることには依存しない。関係する場面（拒否・確認・通すとき・ターンの終わりなど）で、
  その場に要る理由・注意点・手順が届く。「ルールを全部コンテキストに入れてから判定する」仕組みではない
- 検索と必要時の参照（3）: エージェントが情報を必要としたときに、自分で資料や手順を探して読む。
  ccnavi は何を読めばよいかを絞る目録と索引を用意する。

どちらも、不要な情報を最初から入れず、同じ説明を重ねて渡さないための設計である。
ただし、渡す量が減ることやルールが守られることは保証しない。

設計原則とコア機能を支える仕組みは[設計書の 1.1.1](docs/20_design.md)にある。

用語は [CONTEXT.md](docs/CONTEXT.md)、要求は [requirements.md](docs/10_requirements.md)、設計は
[docs/20_design.md](docs/20_design.md)、判断の理由と経緯は [docs/30_adr/](docs/30_adr/README.md) にある。
「承認済みチケット」「マーカー」「ワークツリー」「直接作業」のような呼び名は用語集で定義している。

## 導入

どんなコマンドが打たれ、ルールが何にヒットするかは、動かしてみるまで分からない。`dry-run` で導入し、記録を読みながら
ルールを直し、記録が落ち着いてから `enable` に切り替える。

1. `dry-run` で立てる。実行ファイルを組み立て、hook を登録し、`CCNAVI_MODE` を `dry-run` にする
2. 普段どおり開発する。ccnavi は判定だけを記録し、呼び出しには手を出さない
3. 記録（`logs/decisions.jsonl`）を読む。止めた回、どこにもヒットしなかった回、通した回を数える
4. ルールを直す。直したら `ccnavi --lint` と `ccnavi --test-expectations` で確かめる
5. 記録が落ち着いたら `CCNAVI_MODE` を `enable` にして、セッションを開き直す

手順の詳細と記録を読む `jq` の例は [docs/41_guide/install.md](docs/41_guide/install.md) にある。

## 設定とルールの最小例

設定は環境変数で、`.claude/settings.json` の `env` ブロックに書く。hook には実行ファイルだけを登録すればよい。
`sh scripts/ccnavi-setup.sh <ワークスペースルート>` が、何度打っても同じ形になるよう書く。

```json
"env": {
  "CCNAVI_MODE": "dry-run"
}
```

ルールファイルは YAML で、`deny` `ask` `allow` の 3 つのタイプに分かれる。

```yaml
version: 1

deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。

ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前にユーザが中身を見る。何が変わるかを先に言うこと。
```

環境変数の表と hook の登録は [docs/41_guide/settings.md](docs/41_guide/settings.md)、ルールの書き方は [docs/41_guide/rules.md](docs/41_guide/rules.md) にある。

## 手引き

詳しい手引きは [docs/41_guide/](docs/41_guide/index.md) に置く。

| 知りたいこと | 読む文書 |
|---|---|
| 導入と dry-run から enable まで、配布の打ち方 | [install.md](docs/41_guide/install.md) |
| 環境変数、hook の登録、動作モード | [settings.md](docs/41_guide/settings.md) |
| ルールの書き方 | [rules.md](docs/41_guide/rules.md) |
| Bash のコマンドとパスの読まれ方 | [bash-reading.md](docs/41_guide/bash-reading.md) |
| 実行後チェック、自動復元、コアファイルの保護 | [hooks.md](docs/41_guide/hooks.md) |
| チケット、承認、フェーズ、リスク、テンプレート | [tickets.md](docs/41_guide/tickets.md) |
| レビューの依頼と確認、マージ後の片付け | [review.md](docs/41_guide/review.md) |
| 複数人・別 PC・別セッションで進める | [multi-pc.md](docs/41_guide/multi-pc.md) |
| 記録と振り返り、学びの蓄積 | [records.md](docs/41_guide/records.md) |
| 設定が意図どおり効いているかの確かめ方 | [check.md](docs/41_guide/check.md) |
| ドキュメントの索引 | [docs-search.md](docs/41_guide/docs-search.md) |
| 生の git が止まる理由と打ち方 | [git.md](docs/41_guide/git.md) |
| VS Code と Chrome の拡張 | [extensions.md](docs/41_guide/extensions.md) |
| つまずいたとき | [troubleshooting.md](docs/41_guide/troubleshooting.md) |

開発に参加する人は [docs/40_development.md](docs/40_development.md) へ。
