---
version: 1
ticket: sh-ws-root-04
parent: sh-ws-root
phase: 4
predecessors:
- sh-ws-root-03
human_review:
  required: true
  reason: 次に入る人が読む唯一の手掛かりになるため。特に未了の一覧と、今回見つけた設計の穴の記述
title: 文書 — CLAUDE.md をモード B に合わせ、HANDOVER に残りと穴を書く
rationale: '実装が入ったので、運用の記述をそれに合わせる。CLAUDE.md は今まで

  単一リポジトリの手順しか書いておらず、モード B の読み手が 8 か所で間違える。

  両モードで正しい 1 つの書き方に統一する（2026-09-12 の決定）。

  HANDOVER には、要求表への追記が未了であること、config-union に残る穴、

  Python 側に残る 6 件、今回見つけた ccnavi 自身の設計の穴を書く。

  '
allow:
- match: Write|Edit
  glob: CLAUDE.md
- match: Write|Edit
  glob: HANDOVER.md
ccnavi_approved:
  approved_at: 2026-09-13T04:42:19+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root-04.md
started_at: 2026-09-13T04:43:16+0900
base_sha: ada0431d12e592a393309a8adef0f356bf8174e7
completed_at: 2026-09-13T04:46:19+0900
---

# 文書 — CLAUDE.md をモード B に合わせ、HANDOVER に残りと穴を書く

## CLAUDE.md

今の記述は単一リポジトリ（モード A）の手順しか書いていない。監査で、モード B の
読み手が間違える点が 8 つ挙がっている。**節を分けず、両モードで正しい 1 つの書き方に
統一する。** 読み手に「自分はどちらか」を判断させると、混在（このリポジトリ自身が
そうなる）で間違える。

直すこと。

1. sh の綴り。プロジェクトの中からも届く形にする
2. `worktree add` の行き先。プロジェクトの中では `../../.claude/worktrees/<名前>`。
   知らないオプションが通らなくなったことも書く
3. `<統合先>` がプロジェクトのブランチになる場合があること。`fetch` はプロジェクトの
   中で打つこと
4. 提案の置き場が `wip/<プロジェクト>/tickets/`（ワークスペース側）で、プロジェクトの
   リポジトリには入らないこと
5. `logs/` はワークスペースのもの。プロジェクトのリポジトリに書くのは
   `config/rules.yml` だけ
6. チケットの `project:` の存在と、決めるのは人の承認だということ
7. プロジェクトの置き場は 1 段固定
8. ルールの `message` には `{root}` を使うこと

## HANDOVER.md

次に入る人が読む。4 つ書く。

1. **このチケットで入ったもの**と、写す手順（`wip/design/scripts/COPY.md`）。
   写す前と後の確かめ方
2. **未了**。`ccnavi.md` §25 と `requirements.md` の REQ-MLT 表への追記。
   `config-union` が §25 を構造ごと改版するため先送りした。実装が入っているのに
   要求表に無い期間ができている
3. **別チケットに回した Python 側 6 件**。識別子の接頭辞が消える、プロジェクト名の
   空白で自己防衛が抜ける、プロジェクトから切った作業ツリーの控えが作られない、
   lint の早期 return、孤児の作業ツリーの lint、`message` の `{root}` の lint。
   `config-union` が統合先に入ってから出す
4. **`config-union` に残る見込みの穴**。selfguard がプロジェクト名の区画を
   `[^\\/ \x00]+` で書いているため、名前に空白が入るとルールファイルへの書き込みが
   止まらない。置き場を `.ccnavi/config/` に移しても正規表現は同じなので移植される。
   再現手順付きで書く

## ccnavi 自身の設計の穴（今回見つけたもの）

HANDOVER に書く。今回は回避したが、次に同じことをする人も同じ場所で止まる。

1. **保護済みファイルを直すチケットが行き止まりに入る。** フェーズの種類の `scope` と
   親の `allow` の両方に阻まれ、着手後は親の `plan` の改版もできない（提案が `doing/`
   にあり `builtin-ticket-state` が編集を止める）。今回は `staging` という種類を
   足して回避した
2. **`.claude/scripts/` は git が運ぶが、`.claude/ccnavi/tickets/` と `state/` は
   運ばない。** この非対称が、ワークスペースルートの決め方を難しくしている。
   実装は `.claude/worktrees/` の下を候補から外すことで解いた
3. **シェルでフィクスチャを組み立てると `builtin-guard-setting-files` が反応する。**
   コマンドの文字列に `.claude/scripts` が含まれるだけで当たる。書き込み先が
   一時ディレクトリでも同じ

## 設計書の反映

`wip/design/sh-ws-root.md` の 2 節が、実装で変えた根の決め方を反映していない。
フェーズ 3 のレビューで「フェーズ 4 で直すか、いま直すか」を投げた。**この子で直す。**
`wip/design/*` はこの子の範囲に無いので、範囲に足すか、HANDOVER に差分を書くかは
承認のときに決めてほしい。

## この子で決めないこと

- `ccnavi.md` と `requirements.md` への反映
- Python 側の修正
- 写す作業そのもの（人が行う）
