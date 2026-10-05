---
title: ccnavi ボードのルール管理画面
type: requirements
description: rules.yml を直し、判定を試し、hook を眺める画面のふるまい
tags: [design-doc, extension, rules]
keywords: [要件, ルール管理, ルール, rules.yml, 判定を試す, サンプル, hook, 保存, lint, コメント]
---

# ルール管理画面

同じ拡張に「ルール管理画面」がある。ルールファイル（`rules.yml`）を画面で直し、保存する前に「この操作はどう判定されるか」を試し、hook の一覧を見る。判定は実行ファイルの `--test --json` / `--test-samples --json` で行い、拡張は glob も regex も自分では当てない。

対象は 3 種（ccnavi の README「ルールは 3 つのレイヤーの和で当たる」）。共通の設定のルール（`.ccnavi/common/rules.yml`）、ワークスペースの設定（既定 `.ccnavi/config/rules.yml`）、プロジェクト 1 つの設定（既定 `projects/<名前>/.ccnavi/config/rules.yml`）。サイドパネルからは共通の設定を開く。ワークスペースの設定とプロジェクトの設定は、画面上部の「設定」の欄（共通の設定・ワークスペース・設定のあるプロジェクトの一覧）で切り替える。欄は対象が 2 つ以上あるときだけ出る。タブは 1 枚で、別の対象を選ぶと中身が入れ替わる。未保存の変更があれば、破棄してよいかを聞く。

ワークスペースとプロジェクトの設定ファイルの場所は `--explain --json` の `layers[]` から取るので、実行ファイルが見つからないとその画面はエラーの表示になる。編集中の内容は、共通の設定なら `--rules`、ワークスペースとプロジェクトの設定なら `--project-rules-file <名前>=<パス>`（ワークスペースの設定は `self=<パス>`）で実行ファイルに渡す。

保存を止めるのは、共通の設定とワークスペースの設定ならどのツリーに `doing` があっても（どちらも全ツリーの Bash に当たるため）、プロジェクトの設定ならそのプロジェクトに `doing` があるときだけ。

実行ファイルがこの設定のファイルを読めず空として扱っている（ここのルールは 1 件も有効になっていない）ときは、上部に注意が出る。

タブは 3 つ。

| タブ | 何ができるか |
|---|---|
| ルール | `rules.yml` をタイプ（deny / ask / allow）ごとに一覧し、id・match・glob か regex・message・additionalContext（当たるたびにモデルへ渡す文）・additionalContextOnce（文脈で最初に当たったときだけ渡す文）・additionalContextFile / additionalContextOnceFile を直す。match は手でも書けるし、欄を押すと判定が対象を取り出せるツールの選択肢が出て選べる。欄に書いてある知らない名前も選択肢として並ぶ。message は deny だけで、止められたモデルに届く文。additionalContextFile / additionalContextOnceFile は文に続けて本文を渡すファイルで、ルートからの相対パス。「選択…」で VS Code のダイアログから選べ、外のファイルは入らない。一覧は 1 ルール 1 行（id、match、pattern とメッセージの先頭、コンテキストの有無の ●）で、既定は全部畳んである。行を押すとその下に欄が開き、欄名は欄の左に出る。additionalContext 系の 4 欄は「コンテキストの追加」の 1 行に畳んであり、値があるルールだけ最初から開く。開いた行は id で覚えておき、更新のあとも開いたまま。上の絞り込み欄に打つと、id・match・pattern・message・additionalContext に含む行だけが残る（開いている行は隠れない）。見出しの件数は「一致した数 / 全体」になり、一致しないが開いたままの行があればその数も添える。ask と allow に message の欄は無く、残っていれば消すボタンだけが出る。足す（足した行は開いて出る）・消す・上下に動かす・タイプを移す。タイプの見出しの畳むボタンでそのタイプごと畳める。判定に当たったルールは畳んであっても開く。保存の前に一時ファイルへ書いて `--lint` を通し、error があれば保存しない |
| 判定を試す | ツールと対象（`--test` の subject）を入れて `--test --json` に掛ける。判定・根拠コード・当たったルール（翻訳後の正規表現まで）・返る文面と、そのツールで走る hook を出す。「サンプルを一括で判定」は `--test-samples --json` で見本をすべて回し、期待と食い違ったものを赤く出す。どちらも**編集中の内容**で試す（保存は要らない）。「記録から候補を出す」は `--suggest --json` で、判定の記録から `deny` / `ask` の下書き（`rules:` と `samples:` の組）を並べる。こちらは**保存済みのルール**で確かめた候補で、ルールには足さない（置くのはユーザ） |
| hook | `.claude/settings.json` と `.claude/settings.local.json` の hooks を読むだけの一覧。書き換えない。ユーザごとの設定（`~/.claude/settings.json`）は載らない |

`rules.yml` のキー名は、欄名にマウスを重ねると title として出る。「コンテキストの追加」の中の欄は、キー名をそのまま欄名にしている。対応は次のとおり。

| 欄名 | キー |
|---|---|
| id | `id` |
| ツール | `match` |
| タイプ | `deny` / `ask` / `allow` のどのリストに入れるか |
| パターン | `glob` / `regex`（左の選択が形式、右が値） |
| メッセージ | `message` |
| every | `every`（空なら当たるたびに渡す。`5` なら 5 回に 1 度で、`additionalContextOnce` はその最初の 1 回＝ 5 回目に届く） |
| additionalContext | `additionalContext` |
| additionalContextFile | `additionalContextFile` |
| additionalContextOnce | `additionalContextOnce` |
| additionalContextOnceFile | `additionalContextOnceFile` |

「判定を試す」の「対象」は `--test` の subject。

次のことを守っている。

- **判定は実行ファイルが出す。** 拡張は `--test` の答えを並べるだけで、glob も regex も自分で当てない。
  hook の「走る／走らない」だけは matcher を拡張で当てる（Claude Code の hook の登録であって ccnavi の判定ではない）
- **作業中のチケットがある間は保存できない。** 着手済みのチケット（`.ccnavi/approved/doing/` にあり `started_at` を持つ）が 1 件でもあれば（どのワークツリーでも）、
  編集はできるが保存ボタンが押せない。hook はツール呼び出しのたびにルールを読み直すので、セッションの途中で
  判定が変わるのを避ける。ボードの JSON が読めないときも保存しない（確かめられないときは止めるほうを選ぶ）
- **外で変わったら上書きしない。** 読み込んだときの更新時刻と保存時のそれが違えば止める。編集中に
  `rules.yml` や `settings.json` が変わると上部に「ファイルの変更を検知しました。更新してください。」と出るので、更新してから直し直す
- **コメントを残す。** `rules.yml` のコメントと折り返しは、変えていない場所ではそのまま。変えた欄も
  引用符や折り返しの書き方は元のまま。新しく足すルールは glob / regex を単引用符で囲む
- **記録を汚さない。** 試し打ちは `--ticket-control disable --state "" --log ""` で走らせ、`decisions.jsonl` に残さない
- 上部に `CCNAVI_MODE` が `enable` でないときの注意が出る。試す判定は enable のときの答え
