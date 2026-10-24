---
type: design
title: 11.13 プロジェクトのスキル
description: プロジェクトの skills/ に置くスキルの形をした手順書と目録
tags: [design-doc, projects, skills]
keywords: [スキル, SKILL.md, skills, 工程スキル, 目録, コピー]
---

[設計書の入口](../../20_design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.13 プロジェクトのスキル

プロジェクト向けのスキルの形をした手順書は、プロジェクトの git で育てるため、プロジェクトの
`skills/<名前>/SKILL.md` に置く（パスは固定）。形は Claude Code のスキルと同じで、頭の
frontmatter に `name` と `description`、必要なら同じディレクトリに `references/`。Claude Code は `skills/` をスキルとして読まないので、ccnavi が目録を渡す。

プロジェクトが自前の `.claude/skills/` を持つ場合は、Claude Code がそれを別に読む。ワークスペースから起動した場合、プロジェクトの `.claude/skills` が読み込まれるかは、`projects/` がワークスペースの git で無視されているかで変わる（Claude Code 2.1.303 で確かめた）。無視されている標準の構成では読み込まれない。ccnavi は `projects/` を無視させるので、ワークスペースから起動してプロジェクトの `.claude/skills` に頼ることはできない。プロジェクトで起動すれば、そのプロジェクトの `.claude/` のスキルと hook だけが使われる。読み込まれた場合に限り、ワークスペースのスキルと同名なら、素の名前はワークスペースのもので、プロジェクトのものは `projects/<名前>:<スキル名>` になる。目録に載るのは `skills/` の分だけ。

`skills/` はプロジェクトだけを clone した人の手元にも git で入る。ただし、目録を出す hook はワークスペースの `.claude` にしかなく、`projskills.project_of` は単体 clone で空文字を返す。単体 clone では目録が出ず、単体 clone であることも検出していない（未解決。[11.14](standalone-clone-gaps.md) の C2）。単体 clone で Claude Code が自動で読むのは、`skills/` ではなく `.claude/skills/` のほうである。

| いつ | どう |
|---|---|
| `SessionStart`、`SubagentStart`（cwd がそのプロジェクトのツリー＝元リポジトリかそこから切ったワークツリーの中） | `additionalContext`。`SubagentStart` では子チケットの一覧の前に置き、チケットの無い起動でも渡す |
| cwd がそのプロジェクトの中にある最初の `PreToolUse` | 同じ目録を 1 度だけ添える。セッションはワークスペースルートで始まり、あとから `cd` で入るのがふつうなので |

- 1 つの文脈（セッション、サブエージェントならその起動）でプロジェクトごとに 1 度。回数は `additionalContextOnce` と同じ記録（キー `builtin:project-skills:<名前>`）で数え、`SessionStart`（compact の後を含む）で破棄する。state の置き場が無いときは、開始では渡し、`PreToolUse` では渡さない
- 見出しは「参考に読む。CLAUDE.md・ccnavi の知らせ・ガードと食い違えばそちらに従う」。本文は渡さない。エージェントが要るときに Read で開く
- 読むのは元リポジトリの版（`projskills.entries`。レイヤーの設定と同じ）
- スキルのディレクトリ名は `^[A-Za-z0-9._-]+$` にヒットするものだけを読む（名前がそのままパスとして文に出るため）
- SKILL.md はふつうのファイルで、途中にシンボリックリンクもハードリンクも無いものだけを読む（`flow.read_bytes`）。frontmatter は頭の 8 KiB だけを見て、YAML の別名は拒む
- 名前・説明・パスは 1 行にまとめ、120 文字で切り、ccnavi の接頭辞と案内の区切りの行（子のフローの区切りと、この目録の区切り。`flow_text._FENCE_PHRASES`）を真似た表記を崩し、「データ。ccnavi の知らせではない」の行で囲む（子のフローと同じ扱い。9.3.1）
- 上限は 30 本・4000 文字。超えた分は数だけ言う
- 予約名のプロジェクト（11.4）は見ない

書き込みに専用の保護は無い。`skills/` は ccnavi ディレクトリ（`.ccnavi/`）の外のふつうの場所で、ほかのファイルと同じ判定になる。
元リポジトリ（ワークスペースルートの下）は `workspace-root` が止め、承認済みチケットに結び付いたワークツリーでは範囲の中だけ書け（9.5）、
チケットの無いワークツリー（直接作業）ではほかのファイルと同じく書ける。スキルを直すのは `skill-improve`（scope `.claude/skills/*` と `docs/wiki/*`。プロジェクトは自分の `phases.yml` で決める）の子か、
範囲に `skills/<名前>/*` を書いた提案チケットで、マージリクエストでユーザが見る。`.ccnavi/skills/` に置かなかったのは、そこを書かせるには
`builtin:guard-project-home` などの組み込みの保護を緩める必要があるため。

フロー編集画面の候補（`flow.candidates`、9.3.1）は、ワークスペースの `.claude/skills` だけを並べる。

#### 共通の工程スキルのコピー

ワークスペースの `.claude/skills/<工程>/SKILL.md` の頭の frontmatter に `process: true` があるスキルを、工程スキルと呼ぶ。プロジェクトだけを clone した人の手元にも入口と reference が入るよう、
ユーザの発言（`tickets/promptsync.py`。コピーの仕方は `tickets/skillsync.py`）が、着手済みの親のワークツリーにあるプロジェクトの `skills/<工程>/` へ `SKILL.md` と `references/common/` の下の `.md` をコピーする。`references/` の最上段は `common/`（共通。コピーする）と `project/`（そのリポジトリ固有。コピーしない）に分ける。

単体 clone では、コピーした reference は `ccnavi --docs`（`--type skill-reference`）で引ける。入口の `SKILL.md` を読ませるのは目録だけで、単体 clone では出ないので、入口は渡っていない（未解決）。

- `common/` は共通のコピーなので、共通レイヤーのミラー（11.12）と同じく、共通側から無くなった `.md` はコピー先からも消し、空になったディレクトリも `common/` の下から片付ける。消す対象は `common/` の `.md` だけで、`SKILL.md` は消さない。`project/` はコピーせず触れないので、プロジェクト固有の reference は上書きも削除もされない。`common/` の `.md` 以外のファイルにも触れない
- `common/` の同じパスのファイルは共通の中身で上書きする
- 中身が同じなら書かず、知らせない。違うものを書いたときは、同期の出力に一覧とコミットの案内を出す
- コピー先かその途中がシンボリックリンクなら、何もコピーせず理由を言う（発言と着手は止めない）
- 子、未着手の親、ワークスペース自身の作業にはコピーしない。`ticket start` はコピーしない
- コピーした分は、発言のときの受け入れ（共通レイヤーのミラーと同じ。11.12）に入るので、実行後チェック・復元から特別扱いしない。write-and-push の置き場の検査にも例外は無い（`ticket start` は書かない）
