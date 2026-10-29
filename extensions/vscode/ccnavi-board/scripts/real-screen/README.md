# 実画面確認の道具（real-screen）

本物の VS Code（code-server）に入れた拡張を Chromium で操作し、チケット管理の手順を haiku（`claude -p`）に進めさせて、
文書と画面が初見の相手に通じるかを確かめる開発用の道具。配布物には含めない。準備と評価の観点は
[実画面（code-server の VS Code）で日本語ユーザ目線を確かめる](../../docs/design/real-screen-check.md)。

## 構成（ページオブジェクト）

画面のセレクタと文言は `pages/` に閉じ込め、流れ（`*.js` の入口）は意味の名前で操作を呼ぶ。画面の文言を直したら `pages/` だけ追従する。

| ファイル | 役割 |
|---|---|
| `lib/session.js` | Chromium を起動し、code-server でフォルダを開く。`CCNAVI_CHROMIUM`・`CCNAVI_CODE_SERVER`（既定 `http://127.0.0.1:8080`）で差し替える |
| `pages/WorkbenchPage.js` | VS Code の枠。左の一覧から画面を開く、webview のフレーム、ターミナル、通知、撮影 |
| `pages/BoardPage.js` | チケット管理。案内ツアーを閉じる、列の件数、詳細、`openApproval()`、`clickReviewDone()`、`clickButton(名前)` |
| `pages/ApprovalDialog.js` | 承認の確認と結果の枠。`body()`・`confirm()`・`cancel()` |
| `approve.js` | 先頭の承認待ちを承認する（承認の前後を撮り、画面の文を出す。`--no-confirm` で開くだけ） |
| `snapshot.js` | 左の一覧の画面を順に開き、PNG とテキストを残す |
| `fake-host.sh` | 偽の GitLab と bare を立て、検証用コピーの origin を向ける（チケット作業はホストが要る） |
| `haiku-step.sh` | haiku に 1 手ずつ進めさせる（`--continue` で続き）。`summarize-run.py` が、呼んだ操作と結果の頭だけに要約する |

新しい画面（ルール管理など）を足すときは、`pages/<画面>Page.js` を作り、`WorkbenchPage` を受け取る形にそろえる。

## 通し試験の流れ

```sh
# 0. 検証用コピーを作り、偽のホストへ向ける。code-server は PATH と GITLAB_TOKEN を渡して起動する（拡張の子プロセスが引き継ぐ）
sh scripts/real-screen/fake-host.sh up "$W/host" "$UC"
export PATH="$W/host/bin:$PATH" GITLAB_TOKEN=fake-gitlab-token-0001
# 1. haiku が提案まで進める
echo 'issue #1 を進めてください。' > "$W/p1.txt"
sh scripts/real-screen/haiku-step.sh "$UC" "$W/steps" "$W/p1.txt"
# 2. ユーザ役が実画面で承認する
node scripts/real-screen/approve.js --folder "$UC" --out "$W/shots" --tag parent
# 3. 続きを haiku に頼む（承認後に画面が出す引き継ぎのプロンプトがあれば、それを渡す）
echo '承認しました。続きを進めてください。' > "$W/p2.txt"
sh scripts/real-screen/haiku-step.sh "$UC" "$W/steps" "$W/p2.txt" --continue
```

承認待ち・レビュー待ち・改版の承認ごとに 2 と 3 を繰り返し、`ready` まで進める。レビュー完了は `BoardPage.clickReviewDone()` と
`clickButton("レビュー済みにする")`。`claude -p` の起動は、広い権限を自動で許可する形になるため、実行するシェルの許可が要る。
ターミナルの中身は xterm の描画なので DOM から読めない。確かめるときは撮影する。
