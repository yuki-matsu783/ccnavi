# ADR-0049: 探すツールが読むファイルの守りは、ルールに足さず 3 層に分ける

状態: 採用

## 状況

共通層の `credentials` ルールの `match` は `Bash|Read|Write|Edit|NotebookEdit` で、`Grep` と `Glob` が
入っていない。組み込みの既定（`builtin-credentials`）も `Bash|Read|Write|Edit` で同じ。

一方、`Grep` の対象は探し始める場所のパスで、探した先で読まれたファイルは判定に届かない（ADR-0048）。
`Grep(path=<リポジトリルート>)` は `.env` や `secrets/` の中身を返しうるが、当たるルールは無い。

これを塞ぐべき穴と見て `match` に `Grep|Glob` を足すのか、別の層に預けるのかを決めていなかった。

## 決定

`Grep` と `Glob` を `credentials` の `match` に足さない。探すツールが読むファイルの守りは、
次の 3 層に分ける。ccnavi が持つのは 3 つ目だけ。

| 層 | 何を止めるか |
|---|---|
| `.gitignore`（ripgrep が読む） | 起点から降りていく途中で出会うファイル |
| `.claude/settings.json` の `permissions.deny` の `Read(...)` | ファイル 1 つ 1 つ。`Grep` のファイル読み取りにも効く |
| ccnavi のルール | 守りたい場所を**起点に指定した**呼び出し |

## 理由

`Grep` を `credentials` に足しても塞がらない。当たるのは起点のパスだけなので、`secrets/` を名指しした
検索は止まるが、ルートからの検索は素通りしたままになる。塞がらないものを足すと、止まっているつもりの
範囲だけが広がる。

止まらないほうの呼び出しは、ripgrep が `.gitignore` で弾き、Claude Code の `Read(...)` の `deny` が
ファイル単位で弾く。実行ファイルは探索の中身を見に行かない（ADR-0028 の境界）ので、この分担を崩さない。

## 3 層がどう噛み合うか

`projects/foo/env/himitsu.txt` が foo の `.gitignore` に入っている場合。

| 呼び方 | ccnavi のルール | `.gitignore` | `Read()` の `deny` |
|---|---|---|---|
| `Grep(path=<ルート>)` | 当たらない | **弾く** | **弾く** |
| `Grep(path=.../env)` | **止める** | 当たらない | **弾く** |
| `Grep(path=.../env/himitsu.txt)` | **止める** | 当たらない | **弾く** |
| `Read(.../env/himitsu.txt)` | **止める** | 当たらない | **弾く** |
| `Bash: cat .../env/himitsu.txt` | **止める** | 当たらない | 当たらない |

`.gitignore` と ccnavi のルールは、互いの当たらないところを埋める。ccnavi のルールだけでは
ルートからの検索が残り、`.gitignore` だけでは名指しの検索が残る。

## `.gitignore` が効かないところ

ripgrep の既定の挙動で、ccnavi の側では変えられない。ルールを書く人が当てにしてよい範囲を
決めるので、ここに書き出す。

- **起点そのものに指定されたパスには当たらない。** ignore されたディレクトリやファイルを `path` に
  渡すと、その中は読まれる
- **`.git` が無いディレクトリでは `.gitignore` を読まない**（ripgrep の `--require-git`）。clone
  していない置き場、git 化していない `projects/<名前>/` が該当する
- **降りた先に `.git` があれば、そこから下はそのリポジトリの `.gitignore` が効く。** ワークスペース
  ルートが git 管理下でなくても、中のプロジェクトの `.gitignore` は効く。`.git` がファイル
  （作業ツリーの gitdir ポインタ）でも同じ
- **`Read` ツールには最初から関係しない。** こちらは `credentials` ルールと `Read()` の `deny` が止める

## 得たもの・失ったもの

- 得たもの: 塞がらないルールを増やさない。`credentials` の `match` は `settings.json` の
  `permissions.deny` と同じ範囲のまま並ぶ
- 得たもの: 誤検知が増えない。`Grep` を足すと、起点のパスに `env` や `key` の綴りが入っているだけの
  普通の検索まで止まる
- 失ったもの: 3 層のうち 2 層が ccnavi の外にある。配布先が `permissions.deny` を持たず、対象が
  `.gitignore` にも入っていなければ、ルートからの `Grep` は読める
- 失ったもの: 残る 1 パターン。`.git` を持たないディレクトリを起点にした `Grep` は、`.gitignore` も
  ccnavi のルールも当たらない
- 失ったもの: `--lint` はこれを言わない。`match` に `Grep` を書いたルールは、起点に当たらないだけで
  死んだ行ではないので、咎める基準が無い

## 採らなかった案

| 案 | 採らなかった理由 |
|---|---|
| `credentials` の `match` に `Grep\|Glob` を足す | 止まるのは起点を名指しした検索だけで、ルートからの検索は素通りのまま。塞がらないのに誤検知だけ増える |
| 実行ファイルが探索の中身を自分で調べる | 実行ファイルの境界の外（ADR-0028）。ripgrep と同じ探索を二重に持つことになる |
| `Grep` の判定を実行後の監視で結果に当てる | 読まれた後なので遅い。守りにならない |
| 守りを `.gitignore` だけに預ける | 名指しの検索と `Read` が残る。`.gitignore` は隠す仕組みであって、止める仕組みではない |
