# ADR-0050: 探すツールが読むファイルの守りは、ルールに足さず 3 層に分ける

状態: 採用

## 状況

共通層の `credentials` ルールの `match` は `Bash|Read|Write|Edit|NotebookEdit` で、`Grep` と `Glob` が
入っていない。組み込みの既定（`builtin-credentials`）も `Bash|Read|Write|Edit` で同じ。

一方、`Grep` の対象は探し始める場所のパスで、探した先で読まれたファイルは判定に届かない（ADR-0048）。
`Grep(path=<git プロジェクトルート>)` は `.env` や `secrets/` の中身を返しうるが、当たるルールは無い。

これを塞ぐべき穴と見て `match` に `Grep|Glob` を足すのか、別の層に預けるのかを決めていなかった。

## 決定

`Grep` と `Glob` を `credentials` の `match` に足さない。ccnavi のルールが止めるのは、これまでどおり
`Read` `Write` `Edit` `NotebookEdit` のパスと `Bash` のコマンド文字列だけにする。

探すツールが読むファイルは、ccnavi の外の 2 層が受け持つ。

| 層 | 何を止めるか |
|---|---|
| `.gitignore`（ripgrep が読む） | `Grep` が起点から降りていく途中で出会うファイル |
| `.claude/settings.json` の `permissions.deny` の `Read(...)` | ファイル 1 つ 1 つ。`Grep` のファイル読み取りにも、`cat .env` のようにファイルを名指しする Bash のコマンドにも効く |
| ccnavi のルール | `Read` `Write` `Edit` `NotebookEdit` `Bash`。`Grep` と `Glob` は受け持たない |

## 理由

`Grep` を `credentials` に足しても塞がらない。当たるのは起点のパスだけなので、ルートからの検索は
素通りしたままになる。塞がらないものを足すと、止まっているつもりの範囲だけが広がる。

止まらないほうの呼び出しは、ripgrep が `.gitignore` で弾き、Claude Code の `Read(...)` の `deny` が
ファイル単位で弾く。実行ファイルは探索の中身を見に行かない（ADR-0028 の境界）ので、この分担を崩さない。

## 3 層がどう噛み合うか

`projects/foo/.env` が foo の `.gitignore` に入っている場合。`--test` と実際のツール呼び出しで
確かめた結果。

| 呼び方 | ccnavi のルール | `.gitignore` | `Read()` の `deny` |
|---|---|---|---|
| `Grep(path=<git プロジェクトルート>)` | 当たらない | **弾く** | **弾く** |
| `Grep(path=.../foo/.env)` | 当たらない | 当たらない | **弾く** |
| `Read(.../foo/.env)` | **止める** | 当たらない | **弾く** |
| `Bash: cat .../foo/.env` | **止める** | 当たらない | **弾く** |
| `Bash: grep -rn <語> .` | 当たらない | 当たらない | 当たらない |

`Grep` の行は ccnavi が両方とも空で、`.gitignore` と `Read()` の `deny` だけが受けている。
2 行目は `.gitignore` も外れるので、`Read()` の `deny` が唯一の守りになる。

**最後の行はどの層も受けていない。** Bash の `grep -rn <語> .` はファイルを名指ししないので
`Read()` の `deny` に当たらず、コマンド文字列にも `.env` の綴りが出ないので ccnavi のルールにも
当たらない。GNU の `grep` は `.gitignore` を読まないので、そちらも外れる。共通層の
`prefer-read-grep` が Bash の `grep` から `Grep` ツールへ誘導しているのは、ここでは守りとしても
働く。`Grep` ツールなら `.gitignore` と `Read()` の `deny` の 2 層が掛かる。

## `Read()` の `deny` が Bash に及ぶ範囲

`Read(...)` の `deny` は、`Read` ツールだけでなく **Bash のファイルを名指しするコマンド**にも
効く。Claude Code 2.1.273 で実際に確かめた結果。

| Bash のコマンド | 結果 |
|---|---|
| `cat .env` | **拒否**。ファイルが存在しなくても、コマンドの文字列の段階で止まる |
| `grep SECRET .env` | **拒否**。名指ししているため |
| `> .env`（リダイレクト先） | **拒否**。`Read()` の `deny` がリダイレクトの行き先にも及ぶ |
| `grep -rn <語> .` | **通る**。ファイルを名指ししていない |

公式の文書（[Configure permissions](https://code.claude.com/docs/en/permissions)）が境目を
こう書いている。`Read` と `Edit` の `deny` は、組み込みのファイルツールと、Claude Code が
Bash の中で見分けるファイルのコマンド（`cat` `head` `tail` `sed` `tee` など）と、リダイレクトの
行き先に及ぶ。ファイルを名指ししないコマンド（`grep -rn <語> .`）や、自分でファイルを開く
スクリプトには及ばない。

**この挙動はバージョンに依る。** Claude Code 2.1.257 で Bash の読み取りコマンドへ適用が始まり、
2.1.259 で範囲が広がって行きすぎ（`npm run build` まで拒む）、2.1.260 で一部が戻された。
ccnavi の側で決められることではないので、ここに書いたのは「2.1.273 でそうだった」という記録で、
守りの前提として数えるなら自分の版で確かめ直すのがよい。

ccnavi の `credentials` が `Bash` を受け持っているのは、この適用が入る前からで、いまは重なる。
重なっても外さないのは、配布先の `settings.json` に `deny` が無いことがあるのと、
上の表の最後の行のように、どちらか一方しか当たらない形が残るため。

## `.gitignore` を当てにしてよい範囲

ripgrep の既定の挙動で、ccnavi の側では変えられない。

- **起点そのものに指定されたパスには当たらない。** ignore されたディレクトリやファイルを `path` に
  渡すと、その中は読まれる
- **`.git` が無いディレクトリでは `.gitignore` を読まない**（ripgrep の `--require-git`）。clone
  していない置き場、git 化していない `projects/<名前>/` が該当する
- **降りた先に `.git` があれば、そこから下はそのリポジトリの `.gitignore` が効く。** ワークスペース
  ルートが git 管理下でなくても、中のプロジェクトの `.gitignore` は効く。`.git` がファイル
  （作業ツリーの gitdir ポインタ）でも同じ
- **隠しファイルは弾かれない。** ドットで始まるファイルやディレクトリも、ignore されていなければ
  `Grep` が読む。素の `rg` の既定とは違うので、「ドット始まりだから隠れている」と数えない
- **`Read` ツールには最初から関係しない。** こちらは `credentials` ルールと `Read()` の `deny` が止める

`.ignore`・`.rgignore`・`.git/info/exclude`・git のグローバルな除外（`core.excludesFile`、既定では
`~/.config/git/ignore`）も ripgrep は読む。どれも弾く側に働くので、守りが薄くなる方向には出ない。

## 得たもの・失ったもの

- 得たもの: 塞がらないルールを増やさない。`credentials` の `match` は `settings.json` の
  `permissions.deny` と同じ範囲のまま並び、2 つを突き合わせて読める
- 得たもの: `Grep` の判定を増やさないので、`--test` と記録の読み方が `Read` と揃ったまま
- 失ったもの: 3 層のうち 2 層が ccnavi の外にある。配布先が `permissions.deny` を持たず、対象が
  `.gitignore` にも入っていなければ、`Grep` は読める
- 失ったもの: どの層も当たらない呼び方が残る。少なくとも次の 3 つ。これで全部だとは言えない
  - `.git` を持たないディレクトリを起点にした `Grep`。`.gitignore` が読まれず、ルールも当たらない
  - ignore されたディレクトリやファイルを起点に名指しした `Grep`。`Read()` の `deny` が
    その綴りを持っていなければ通る
  - Bash の `grep -rn <語> .`。ファイルを名指ししないので `Read()` の `deny` に当たらず、
    コマンド文字列にも守りたい綴りが出ないので ccnavi のルールにも当たらない。GNU の `grep` は
    `.gitignore` も読まない
- 失ったもの: `--lint` はこれを言わない。`match` に `Grep` を書いたルールは、起点に当たらないだけで
  死んだ行ではないので、咎める基準が無い

## 採らなかった案

| 案 | 採らなかった理由 |
|---|---|
| `credentials` の `match` に `Grep\|Glob` を足す | 当たるのは起点のパスだけ。しかも `credentials` の regex は `[\\/]secrets[\\/]` のように前後の区切りを要求するので、`Grep(path=.../secrets)` のようにディレクトリそのものを起点にした形すら当たらない（当たるのは `.../secrets/x` のように中を名指ししたときだけ）。ルートからの検索は素通りのまま |
| プロジェクトの層に `Grep`・`Glob` に絞ったルールを置く | ADR-0048 でプロジェクトの層も `Grep` に効くようになったので書けるが、当たるのはやはり起点のパスだけ。共通層に足すのと同じ理由で塞がらない。誤検知をそのプロジェクトの中に閉じられる利点はあるが、閉じる先の守りが無い |
| 実行ファイルが探索の中身を自分で調べる | 実行ファイルの境界の外（ADR-0028）。ripgrep と同じ探索を二重に持つことになる |
| `Grep` の判定を実行後の監視で結果に当てる | 読まれた後なので遅い。守りにならない |
| 守りを `.gitignore` だけに預ける | 名指しの検索と `Read` が残る。`.gitignore` は隠す仕組みであって、止める仕組みではない |
