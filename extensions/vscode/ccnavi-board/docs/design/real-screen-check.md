---
title: 実画面（code-server の VS Code）で日本語ユーザ目線を確かめる
type: design
description: ccnavi の最新ソースを検証用コピーへ setup で反映し、拡張を入れた code-server の VS Code を Chromium で撮って、日本語ユーザにとって分かりづらい点を洗い出す手順。準備、詰まりどころの表、画面と操作ごとの撮り方、評価の観点、未確認のこと
tags: [design-doc, extension, testing]
keywords: [実画面, code-server, 日本語, ユーザ目線, 画面確認, Chromium, playwright-core, 検証用コピー, ccnavi-setup.sh, vsix, 案内ツアー, 詰まりどころ, スクリーンショット, 評価の観点]
---

# 実画面（code-server の VS Code）で日本語ユーザ目線を確かめる

[設計の入口に戻る](../design.md)

ボードの画面だけなら [render-board.js](development.md#画面を-vs-code-なしで描くrender-boardjs) で描ける。ここでは、その先を確かめる。
本物の VS Code（左の一覧、タブ、通知、ターミナルを含む）に拡張を入れ、ccnavi を入れたリポジトリを開いて、操作しながら撮る。
目的は、日本語で読む利用者にとって分かりづらい画面や使いづらい画面を見つけることにある。

動かした環境は Linux（クラウドのコンテナ、root）で、外へ出られるのは npm のレジストリだけだった。
VS Code 本体の配布元、GitHub のリリース、Open VSX は 403 で、`code` や `@vscode/test-electron` は使えない。
そのため VS Code は npm の `code-server`（ブラウザで開く VS Code）で代える。手順は Linux で動かした。macOS や Git Bash では、`setsid`・`disown`・`python3` を読み替える（未確認）。

## 1. 前提と準備

必要なもの。

| もの | 用途 | 確かめ方 |
|---|---|---|
| Node.js 22.13.1 以上と npm | 拡張の組み立て、code-server | `node -v` |
| pnpm 10 | 拡張の組み立て | `pnpm -v`（壊れていたら詰まりどころの表） |
| uv と Python 3.12 以上 | ccnavi の組み立て、チケットの見本を作る | `uv --version` |
| Playwright の Chromium | 撮影 | `ls -d /opt/pw-browsers/chromium-*/chrome-linux*/chrome` |
| git | 検証用コピーのワークツリー | `git --version` |

以降で使う変数。値は自分の環境に合わせて直す。

```sh
CCNAVI=/home/user/ccnavi            # ccnavi のリポジトリ
TESTREPO=/home/user/test-repo       # 配布先の見本。ここは触らない
UC=/home/user/uc-repo               # 検証用コピー（チケットを並べて撮る）
W=${TMPDIR:-/tmp}/ccnavi-real       # 作業と画像の置き場。リポジトリの外
BOARD=$CCNAVI/extensions/vscode/ccnavi-board
CHROME=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux*/chrome | head -1)
mkdir -p "$W/shots"
```

### 1.1 ccnavi を組み立てる

```sh
cd "$CCNAVI" && uv run --with pyinstaller python build.py
```

約 10 秒で終わる。`dist/ccnavi/` ができ、ccnavi 自身の `.ccnavi/bin/<os>-<arch>/` にもコピーされる（どちらも git 管理外）。`ccnavi-setup.sh` は、`dist/` を配布元にする。

### 1.2 検証用コピーを作って setup で反映する

撮影では承認など書き込みを伴う操作をするため、test-repo 本体ではなくコピーで行う。

```sh
rm -rf "$UC" && cp -a "$TESTREPO" "$UC"
cd "$UC" && git remote remove origin        # 承認の操作が push するので、必ず外す（詰まりどころの表）
sh "$CCNAVI/scripts/ccnavi-setup.sh" "$UC" --mode enable --force
```

コピーなので `--force` を付けてよい。`--force` は、配布先にある配布物（設定 3 本の `rules.yml`・`risks.yml`・共通の `phases.yml`、`.ccnavi/` の文書・スクリプト・テンプレート、スキル、`settings.json` の env）を、配布元の内容で置き換える。
test-repo 本体に反映するときは `--force` を付けない（付けると検証用の設定が消える）。付けて置き換えてしまったら、`git diff --stat -- .ccnavi .claude` で差分を見て、残したいものだけ `git checkout -- <ファイル>` で戻す。

### 1.3 チケットを並べる（親 2・子 4）

何も無いリポジトリではボードが空で、確かめられる画面が少ない。ccnavi のテストが使う見本の組み立て（`tests/ticketsample.py`）で、次の状態を作る。

- 親 A: 承認済み。調査の子は完了、設計の子は作業中（ワークツリーあり）、実装の子は承認待ち
- 親 B: 承認待ち（子 1 件も承認待ち）

次を `$W/seed.py` に保存して実行する。

```python
# seed.py: python3 seed.py <検証用コピー> <ccnavi のリポジトリ>
import os, subprocess, sys

root, ccnavi = sys.argv[1], sys.argv[2]
sys.path.insert(0, ccnavi)
from tests.ticketsample import parent_text, child_text  # noqa: E402

phases = open(f"{root}/.ccnavi/config/common/phases.yml", encoding="utf-8").read()
A, B = "feature-101-ログイン画面", "fix-102-日本語の文字化け"


def git(*args, cwd=root):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True)


def put(rel, text):
    fam = A if A in rel else B  # チケットは親のワークツリーに置く
    path = os.path.join(root, ".claude/worktrees", fam, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8", newline="").write(text)


def parent(name, title, plan, issue):
    text = parent_text(name, plan, issue=issue, copy=phases).replace("title: 親", f"title: {title}")
    return text.replace("reason: t", "reason: 画面の見た目が変わるため")


def child(name, par, phase, title, scope, review=True):
    return child_text(name, par, phase, scope, review).replace(
        f"title: 子 {name}", f"title: {title}"
    )


for fam in (A, B):  # 親ごとにブランチとワークツリー（無いと承認も着手もできない）
    git("branch", fam)
    git("worktree", "add", f".claude/worktrees/{fam}", fam)

started = 'started_at: "2026-10-20T09:00:00+09:00"'
done = 'completed_at: "2026-10-20T11:00:00+09:00"'
a = parent(A, "ログイン画面を追加する", ["research", "design", "implement"], 101)
put(f".ccnavi/approved/doing/{A}.md", a.replace('started_at: ""', started))
c1 = child(f"{A}-01-01", A, 1, "ログイン方式の調査", ("wip/research/*",), False)
put(
    f".ccnavi/approved/done/{A}-01-01.md",
    c1.replace('started_at: ""', started).replace('completed_at: ""', done),
)
c2 = child(f"{A}-02-01", A, 2, "ログイン画面の設計", ("wip/design/*",))
put(f".ccnavi/approved/doing/{A}-02-01.md", c2.replace('started_at: ""', started))
put(
    f"wip/proposals/todo/{A}-03-01.md",
    child(f"{A}-03-01", A, 3, "ログイン画面の実装", ("src/*", "tests/*")),
)
put(
    f"wip/proposals/todo/{B}.md",
    parent(B, "CSV 出力の日本語が文字化けする不具合を直す", ["quick-work"], 102),
)
put(
    f"wip/proposals/todo/{B}-01-01.md",
    child(f"{B}-01-01", B, 1, "文字コードを UTF-8（BOM 付き）にそろえる", ("src/*", "tests/*")),
)

for fam in (A, B):
    wt = f"{root}/.claude/worktrees/{fam}"
    git("add", "-A", cwd=wt)
    git("commit", "-qm", f"チケット: {fam}", cwd=wt)
# 設計の子は着手済み（子のワークツリーを切る）
git("branch", f"{A}-02-01", A)
git("worktree", "add", f".claude/worktrees/{A}-02-01", f"{A}-02-01")
```

```sh
python3 "$W/seed.py" "$UC" "$CCNAVI"
sh "$UC/.ccnavi/scripts/ccnavi-launcher.sh" --lint | tail -3    # error 0 件になること（warn は出てよい）
```

コピー元（`$TESTREPO`）にコミット済みの内容から親のワークツリーを切るので、コピー元の未コミットの変更は反映されない。コピーに `ccnavi-setup.sh` を回したあとは、その変更を `git add -A` と `git commit` してから `seed.py` を回す（しないと、親のワークツリーに setup の結果が入らない）。やり直すときは 1.2 から（同じ名前のブランチが残ると止まる）。

`--lint` が error になるときは、親のワークツリー（`.claude/worktrees/<親>`）が無いか、子の `allow` が親の範囲を超えている。

下書きの取り込み（取り込み待ち画面）を撮るときだけ、下書きも作る。

```sh
cd "$UC" && sh .ccnavi/scripts/ccnavi-stage.sh .ccnavi/config/common/risks.yml .ccnavi/docs/worktree.md .claude/settings.json
sed -i 's/medium: 20/medium: 15/' wip/handoff/.ccnavi/config/common/risks.yml
printf '\n追記: 新しい手順\n' >> wip/handoff/.ccnavi/docs/worktree.md
```

手で `wip/handoff/` に置いたファイルは、階層名の読み替え（`handoff-.claude` など）が合わないと「対象外」に数えられる。必ず `ccnavi-stage.sh` で作る。

何も無い状態（編集の確認用）は、チケットを並べない別のコピーで作る。

```sh
ED=/home/user/ed-repo
rm -rf "$ED" && cp -a "$TESTREPO" "$ED"
cd "$ED" && git remote remove origin && git remote -v       # 何も出ないこと
sh "$CCNAVI/scripts/ccnavi-setup.sh" "$ED" --mode enable --force
```

チケットが作業中だと、ルール・リスク・フェーズは保存できないため、編集の操作は、チケットを並べないコピーで撮る。

### 1.4 拡張の vsix を作る

```sh
cd "$BOARD" && sh scripts/package.sh | tail -2     # $CCNAVI/dist/ccnavi-board-<version>.vsix
```

`package.sh` が `pnpm install --frozen-lockfile` と `pnpm run compile` を含む。この `node_modules/` に `playwright-core` も入るので、撮影の道具もこれを使う。

### 1.5 code-server を入れて起動する

```sh
mkdir -p "$W/cs" && cd "$W/cs"
npm install code-server --ignore-scripts --no-audit --no-fund
(cd node_modules/code-server/lib/vscode && npm install --omit=dev --ignore-scripts --no-audit --no-fund)
```

`--ignore-scripts` を外すと、ネイティブモジュール（`kerberos`）の node-gyp が失敗して入らない（詰まりどころの表）。

拡張は `--install-extension` が失敗するので、vsix を展開して拡張の置き場へ置く。

```sh
VER=$(node -p "require('$BOARD/package.json').version")
VSIX="$CCNAVI/dist/ccnavi-board-$VER.vsix"
rm -rf "$W/x" "$W/ext" && mkdir -p "$W/x" "$W/ext" "$W/ud/User"
python3 -m zipfile -e "$VSIX" "$W/x"
cp -r "$W/x/extension" "$W/ext/local.ccnavi-board-$VER"
echo '{"workbench.startupEditor":"none","security.workspace.trust.enabled":false,"telemetry.telemetryLevel":"off","update.mode":"none","extensions.autoCheckUpdates":false,"workbench.tips.enabled":false}' > "$W/ud/User/settings.json"
```

テーマは設定に書かない（書かないと明るい配色で撮れた）。起動する。`setsid` と `< /dev/null` を付けないと、呼び出したシェルが終わったときに落ちる。

```sh
cd "$W/cs" && setsid nohup node node_modules/code-server/out/node/entry.js \
  --auth none --bind-addr 127.0.0.1:8080 --user-data-dir "$W/ud" --extensions-dir "$W/ext" \
  --disable-telemetry --disable-update-check "$UC" > "$W/server.log" 2>&1 < /dev/null & disown
curl -s http://127.0.0.1:8080/healthz      # {"status":...} が返る
```

別のフォルダを開くには、サーバーは動かしたままにして URL の `?folder=<絶対パス>` を変える。
止めるときは `pgrep -f "[c]ode-server/out/node/entry"` で PID を見て `kill <PID>` する。`[c]` と書くのは、`pgrep -f`・`pkill -f` が自分のコマンドラインにも当たってシェルごと落ちるのを避けるため。

### 1.6 撮影の道具

次を `$W/shoot.js` に保存する。左の「画面」一覧から画面を開き、案内ツアーを閉じて、撮る。

```js
// shoot.js: node shoot.js <フォルダ> <出力名の接頭辞> <幅> <高さ> <画面の名前>...
// 画面の名前は、左の ccnavi の一覧に出ているもの。画像は $W/shots/<接頭辞>-<番号>-<画面>.png
const { chromium } = require(`${process.env.BOARD}/node_modules/playwright-core`);
const [folder, tag, w, h, ...screens] = process.argv.slice(2);

async function webview(page) {
  for (const f of [...page.frames()].reverse()) {
    const ok = await f.evaluate(() => !!document.querySelector("#root") && document.body.innerText.length > 5).catch(() => false);
    if (ok) return f;
  }
  return null;
}
async function skipTour(page) {  // 案内ツアーは × の 1 回で全部閉じる
  const f = await webview(page);
  const btn = f && f.locator('[data-action="tour-skip"]');
  if (btn && (await btn.count()) > 0) { await btn.first().click(); await page.waitForTimeout(800); }
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME });
  const page = await browser.newPage({ viewport: { width: +w, height: +h } });
  await page.goto(`http://127.0.0.1:8080/?folder=${folder}`);
  await page.waitForSelector(".monaco-workbench", { timeout: 60000 });
  await page.waitForTimeout(6000);
  await page.keyboard.press("Control+Alt+b");                         // 右のチャットを閉じる
  await page.keyboard.press("Control+k"); await page.keyboard.press("w");  // 開いているタブを閉じる
  await page.locator('.activitybar a[aria-label*="ccnavi"]').first().click();
  await page.waitForTimeout(2500);
  let n = 0;
  for (const name of screens) {
    await page.locator(".pane-body .monaco-list-row", { hasText: name }).first().click();
    await page.waitForTimeout(5000);
    await skipTour(page);
    await page.screenshot({ path: `${process.env.W}/shots/${tag}-${String(++n).padStart(2, "0")}-${name}.png` });
  }
  await browser.close();
})();
```

操作を伴う撮影は、この雛形を写し、画面を開いたあとに `webview(page)` で得たフレームを操作する（3 章）。

```sh
export BOARD CHROME W
node "$W/shoot.js" "$UC" board 1280 760 チケット管理      # 最初の 1 枚
```

## 2. 詰まりどころ

| 症状 | 原因 | 対処 |
|---|---|---|
| `pnpm -v` が `Cannot find module '…/corepack/v1/pnpm/12.15.0/bin/pnpm.cjs'` で落ちる | `/opt/node22/bin/pnpm` が corepack の shim で、指す先の pnpm（12.15.0）がキャッシュに無く、ネットワーク制限で取れない | `npm i -g pnpm@10`。入れたあと `hash -r; pnpm -v` が 10.x を返す |
| `ccnavi-setup.sh --force` のあと、test-repo の `rules.yml`・`risks.yml` が配布元の既定に変わっていた | `--force` は配布先にあるものを置き換える。test-repo の検証用の設定も対象 | 作業は検証用コピーで行う（1.2）。本体は `--force` なしで反映する。上書きしたら差分を見て、必要なものを `git checkout -- <ファイル>` で戻す |
| 検証用コピーで承認の操作をしたら、コピーの `origin`（test-repo の実物）に親のブランチが push された | 承認のあとにボードが送るコミット・push の sh が、コピーの `origin` へ push する | コピーを作った直後に `git remote remove origin`（1.2）。本体へ push してしまったときは、そのブランチを削除する（ユーザに確かめてから） |
| 最初に開いた画面が「案内のための見本」で、実際のチケットが出ない | 初回起動（その画面の案内をまだ閉じていない）は、案内ツアーが見本のデータで始まる | 案内の × を 1 回押す（`[data-action="tour-skip"]`）。閉じたあとに実データへ替わる。撮るたびに閉じる処理を入れる（`shoot.js` の `skipTour`）。表示が出ない画面もあるので、あれば閉じる、の形にする |
| `npm install code-server` が `sh ./postinstall.sh` で失敗する（root では `--unsafe-perm` を要求される） | postinstall がネイティブモジュールをビルドする。`--unsafe-perm` を付けても `kerberos` の node-gyp が失敗する | `--ignore-scripts` で入れる。ネイティブモジュールが無くても、画面の確認には足りた |
| code-server に繋ぐと 500 で `Cannot find package '@microsoft/1ds-core-js'` | `--ignore-scripts` では、`lib/vscode/` の依存が入らない | `(cd node_modules/code-server/lib/vscode && npm install --omit=dev --ignore-scripts)`（1.5） |
| `--install-extension <vsix>` が `Got error from Code` で失敗する | 原因は調べていない（ネイティブモジュールを入れていない影響の可能性） | vsix を `python3 -m zipfile -e` で展開し、`extension/` を `--extensions-dir` の下に `local.ccnavi-board-<版>` で置く（1.5）。左の一覧に ccnavi が出る |
| `pkill -f code-server/out/node/entry.js` を、その文字列を含む呼び出しの中で打ったら、シェルごと落ちた（終了コード 144） | `pkill -f` が、自分のコマンドラインにも含まれる文字列に当たった | 止めるのと起動は別の呼び出しにし、`pgrep -f "[c]ode-server/out/node/entry"` で PID を見て `kill` する。起動は `setsid nohup … < /dev/null & disown` |
| Playwright が `strict mode violation`（同じ placeholder の入力欄が 16 個）や `element is not visible` で落ちる | ルールなどの一覧は、行ごとに隠れた入力欄を持つ。新しく足した行は値が空 | `input[placeholder="git-push"][value=""]` や `textarea.f-message:visible` のように、空の値・見えているものに絞る |
| `setsid nohup … & disown` で code-server を起動しようとすると、自動モードの判定器に「危険」と拒否される | 認証なし（`--auth none`）の待ち受けを切り離して動かす操作が危険と見なされた（理由は出ない） | `PASSWORD=<任意> … --auth password` にして、`setsid` を付けずバックグラウンド実行（Bash の `run_in_background`）で起動する。`shoot.js` は先に `/login` でパスワードを入れてから `?folder=` を開く。止めるのは `pgrep -f "[c]ode-server/out/node/entry"` の PID を `kill` |
| プロジェクト管理で clone の URL にローカルのパスを入れると、「指定できる形式は https://host/path、ssh://host/path、git@host:path の 3 つです」で止まる | 画面が `file://` とローカルのパスを拒否する（仕様）。外へ出られない環境では、本物の remote が無い | 見本の bare リポジトリを作り、code-server の起動時に環境変数 `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.<bare の置き場>/.insteadOf GIT_CONFIG_VALUE_0=https://example.test/g/` を付ける。ワークスペースの `.git/config` に書いても、clone には効かなかった（ターミナルは `CONNECT tunnel failed, response 403`）。clone の URL は `https://example.test/g/repo.git` を入れる |
| 行 27 の 2 つ目（同じ URL を送る）で、「既に clone しています」ではなく「repo という名前のツリーが既にあります」が出る | 名前を空にして送ると、URL から付く名前が既存のプロジェクトと重なる。拡張は名前の衝突を先に調べていたので、同じリポジトリだと言わなかった（実機でも同じ。0.12.0 までの挙動。`src/projects-panel.ts` の `clone` で、同じリポジトリの確認を先にして直した）。前の回の説明（origin が書き換え後のパスになって比べられない）は誤り | 直したあとは、同じ URL を送れば「同じリポジトリを <名前> として既に clone しています」が出る。0.12.0 以前の vsix では、別の名前を入れて送ると同じ文が出る |
| clone が終わっても、プロジェクト管理の一覧が 20 秒待っても増えない（「プロジェクト 0 件」のまま）。「更新」を押すと出る | code-server では、ターミナルの終了で一覧が読み直されなかった。実機の VS Code でも起きるかは未確認 | 撮る前に「更新」を押す。実機で行 25・26 を通すとき、「終わると一覧が更新されます」の文のとおり自動で増えるか見る（増えなければ拡張の不具合） |
| ワークスペース直下の `.git` 付きディレクトリは、見本の `参考/` のように作れば認識外の枠に出る。`projects/` を消して作り直すとき、書き込んだ `.gitignore` も `git checkout -- .gitignore` で戻す | 前の回の操作（行 28）が残る | 検証用コピーを `rm -rf` と `cp -a` で作り直すか、上の 2 つを戻してから回す |
| 行 34・35・36 を test-repo のコピーで回すと、画面の「ワークスペース」が `.ccnavi/config/common/risks.yml` を指す（行 34 の期待する `config/project/risks.yml` にならない） | test-repo には `config/project/risks.yml` が無く、共通の設定がワークスペースの設定を兼ねる（行 37 の「無ければ作る」と同じ状況） | 行 34 の path は、ccnavi 自身のリポジトリか、`project/risks.yml` を置いたコピーで見る。行 35・36 の動きは、共通の設定を開いたこの状態でも確かめられた |
| フェーズ・リスク管理で「設定」の欄を切り替えようとして、Playwright の click が `monaco-dialog-modal-block` に遮られて 30 秒で落ちる | 保存していない編集があると、切り替えの前に VS Code の確認ダイアログが出る（`.monaco-dialog-box`） | 編集を戻して保存するか、スクリプトを分けて、操作ごとに画面を開き直す。操作の途中で「設定」の欄を切り替えない |
| フェーズ管理の「上へ」「下へ」を `getByRole("button", {name: "上へ"})` や `:has-text("上へ")` で押そうとして見つからない | ボタンの文字は「上へ」ではなく `title`（`aria-label`）に持つ。畳んだ行にも隠れた同名のボタンがあり、`.first()` は隠れた方に当たる | `locator('button[title="上へ"]').locator("visible=true").first()` のように、見えているものに絞る。行は先に `button.twist` で開く |
| 行 29 の「判定を試す」で、ヒットしたルールの id が `rules.yml:docs-guard` と出る（表の旧い期待は `<名前>:...`）。同じルールが `ccnavi --explain` では `repo:docs-guard` | 不具合ではない。判定は編集中の内容を一時ファイルに書いて実行ファイルへ渡し、画面がそのパスを `rules.yml` に縮める（`src/core/rules-view.ts` の `tmpName`）。画面の判定では、どのレイヤーのルールかは id から分からない | 判定の確認は、ヒットしたルールの id の文字ではなく、deny の有無とメッセージで見る。プロジェクトのルールが効いているかは、保存してから `ccnavi --explain` の `■ rules <名前>` で見る |
| 作業中のチケットが要る行（30・38・44）で、test-repo のコピーに親子のチケットを手で作ろうとして詰まる | チケットは親のワークツリーに置き、`--lint` が通る形でないと読まれない | 1.3 の `seed.py`（この文書のコード）を、そのまま写して使う。`git -c user.name=t -c user.email=t@t` を付ける。コピーは `setup` のあとにコミットしてから `seed.py` を回す（ワークツリーがコミット済みの内容から切られる） |
| 行 25d で `ccnaviBoard.binPath` を存在しないパスにしても、一覧が普通に出る（失敗の文が出ない） | 設定のパスにファイルが無いと、探す順の次の候補（`.ccnavi/scripts/ccnavi-launcher.sh` など）へ進む | 失敗させるには、実行ビットのある sh（`#!/bin/sh` と `echo boom >&2; exit 1`）を指す。そうすると「ccnavi --explain --json が失敗しました: boom」が出て、古い一覧は残らない。設定はワークスペースの `.vscode/settings.json` に書くと、サーバーを再起動せずに効く |
| 行 30 で、プロジェクトの子チケットが作業中でも「保存」が止まらない。または別のプロジェクトの保存が `--lint` の error で通らない | 行 30 の子チケットは、ワークスペース側の `.claude/worktrees/<親>` に置いた、プロジェクトのリポジトリのワークツリーの中にある。`seed.py` をプロジェクトのルートへ回して作った worktree は、ボードの対象（`trees`）に入らない。プロジェクトのルートの `.ccnavi/approved/doing/` に直接置いた場合は数えられるが、親のワークツリーが無いので `--lint` が error になり、どのプロジェクトの保存も止まる | プロジェクトの git（`projects/repo`）で `git worktree add <ワークスペース>/.claude/worktrees/<親> <親>` と、子の分を切り、親のワークツリーの `.ccnavi/approved/doing/` に親と子を置いてコミットする（`tests/ticketsample.py` の `parent_text`・`child_text`）。`--explain --json` で `tickets` に `project: repo`・`in_progress: true` が出て、`--lint` が error 0 件になるまで確かめてから回す |
| ルール管理で、1 つ目の「設定」の編集を保存せずに別のプロジェクトへ切り替えると、Playwright が確認ダイアログに遮られる | 保存していない編集があると、切り替えの前に確認ダイアログが出る | 切り替えごとにブラウザを開き直す（スクリプトに設定の名前を引数で渡して回す）。同じ id のルールを 2 回足すと、`--lint` の warn（id の重複）が出る。id は回ごとに変える |
| 行 25b で、表示中に別のターミナルから `projects/` へ clone しても、8 秒待っても一覧が増えない | 表示中は、ファイルの変更で一覧が読み直されなかった（隠してから戻すと増える）。code-server のファイル監視のせいか、拡張の不具合かは未確認 | 実機で確かめる。撮るときは、画面を一度隠して戻すか「更新」を押す |
| 作業中のチケットが要る行の「`finish` にすると押せる」を確かめたいが、`finish` の流れ（レビュー待ち）を作るのが重い | `finish` はレビュー・承認の状態を要求する | 近似として、各ワークツリーの `.ccnavi/approved/doing/*.md` を `.ccnavi/approved/done/` へ移す。帯が消え、編集すると保存が押せるようになる（行 38・44 はこれで通った。本物の `finish` は未実施） |
| 画面を開いた直後に撮ると、中身が空や古い | webview は拡張が中身を渡すまで読み込み中 | 開いてから 5 秒待つ。ボードは `ccnavi --explain --json` を呼ぶので、遅いときは増やす |
| VS Code の表示が英語のまま（`locale: ja` を設定しても変わらない） | 日本語言語パックを入れていない（Open VSX に出られない） | 未確認の宿題（5 章）。拡張の文言は日本語で出るので、ccnavi の画面の確認には影響しない |

code-server 由来のため、評価には入れなかったもの。

- ターミナルの表示の崩れ（承認のあとにターミナルへ送る長いコマンドの折り返しで、文字が重なる）。実機の VS Code での再現は未確認
- 等幅フォントでバックスラッシュが「¥」に見えること（正規表現が `¥b` や `¥s` に見える）。この環境のフォントの選び方による可能性があり、実機（Windows の日本語フォント）では未確認
- 起動時に開く code-server の「Walkthrough」のタブ。拡張とは関係ない（`shoot.js` で閉じようとしても残る。撮れた画像では無視する）
- 画面の右上の「チャット」は code-server の機能。`Ctrl+Alt+B` で閉じる

## 3. 画面ごとの撮り方

画像の名前は `<状況>-<番号>-<画面か操作>.png`。撮る幅は 1280×760（日本語の Windows で拡大率 125〜150% のときに近い）を基本にし、狭い幅は 900 を使う。

### 画面

左の ccnavi の一覧から開く。`shoot.js` に名前を並べると、順に開いて撮る。

```sh
node "$W/shoot.js" "$UC" uc 1280 760 チケット管理 ルール管理 リスク管理 フェーズ管理 テンプレート管理 プロジェクト管理
```

| 画面 | 行き方 | 撮る状態 |
|---|---|---|
| チケット管理 | 一覧から | チケットを並べた状態（1.3）。列、カード、上部の件数と絞り込み |
| ルール管理 | 一覧から | 作業中のチケットがある状態と、無い状態（編集できるか）の 2 つ。「ルール」「判定を試す」「hook」の 3 つのタブ |
| リスク管理 | 一覧から | 同上 |
| フェーズ管理 | 一覧から | 同上。共通の定義の一覧 |
| テンプレート管理 | 一覧から | 空の状態 |
| プロジェクト管理 | 一覧から | 空の状態（`projects/` が無い警告を含む） |
| 取り込み待ち | ルール・リスク・フェーズ・テンプレートの各管理画面の「取り込み待ちを開く」（一覧には無い） | 下書きがある状態と、無い状態（1.3 の下書き） |
| 計画編集 | チケット管理の、承認待ちの親のカードの計画を編集するボタン | 計画の図 |
| フロー編集 | チケット管理のカードのフローのボタン | 図と右のペイン。図の文字が既定の倍率で読めるか |

### 操作

画面を開いて案内を閉じたあと、フレームを操作して撮る。ボタンの文言は変わることがあるため、`getByRole('button', { name: /…/ })` の正規表現は画面を見て合わせる。

| 操作 | 状態の作り方 | 撮るもの |
|---|---|---|
| 承認 | チケット管理で、承認待ちを承認するボタン（上部）を押す | 開いた直後（上端までスクロール）、途中、承認する前。承認したあとの結果（検証用コピーで、`origin` を外してあること）。画面の下のターミナルも入れる |
| カードの詳細 | カードの「詳細」を開く | 展開した状態 |
| ルールの追加・保存・判定 | 作業中のチケットが無いコピーで、ルール管理の「ルールを追加」。id・ツール・パターン・メッセージを入れて保存。「判定を試す」タブでツールと対象を入れて判定。期待する判定の一括確認と、記録から候補を出す | 追加フォーム、保存後の通知、判定の結果、一括確認のエラー |
| リスクの項目の追加 | 同上、リスク管理の「項目を追加」 | 追加フォーム |
| フェーズの定義の追加とヘルプ | 同上、フェーズ管理の「定義を追加」「ヘルプ」 | 追加フォーム、ヘルプを開いた状態 |
| テンプレートを作る | テンプレート管理で名前を入れて作る | 作った直後（検証のエラーが出るか） |
| 不正な clone URL | プロジェクト管理で、URL 欄に URL ではない文字列を入れて clone | 欄の下のエラー、名前欄の自動入力 |
| 下書きの取り込み | 取り込み待ちで、すべて選ぶ→選んだ対象を取り込む | 確認の枠。ターミナルでしか取り込めない対象の表示 |
| 幅 900px | `shoot.js` の幅を 900 にして、チケット管理とルール管理 | 列の切れ、ボタンの折り返し |

操作の撮影は、`shoot.js` の `for` を、その操作の手順に置き換えた別のファイルにする。1 画面 1 操作ずつ、操作の前後で `page.screenshot` を呼ぶ。

## 4. 評価の観点

日本語で読む利用者にとって分かりづらいか、使いづらいかを、次の物差しで見る。

- 実装の語がそのまま出る（`allow`・`deny`・`after`・`plan:`・`feedback:`・コマンドや `sh` の長文）。意味を知らない人が読めるか
- 英語の例外文や、`Errno`・スタックのような内部の文言が出る
- 一時パスや環境固有のパスが出る（`../../../tmp/…`）
- 初見で何をするものか分からないバッジ、ボタン、見出し。同じものに別の名前を付けていないか
- 日本語の中に英語だけの見出し・項目名・値が混ざる（判定の結果の項目名など）
- 識別子（長い ID）がタイトルより目立つ。ID が単語の途中で折り返される
- 幅で切れる・折れる（4 列目が見えない、プレースホルダーが途中で切れる、ボタンが 2 段になる）
- 何もしていないのにエラーの見た目が出る。赤い帯が、通常の案内に使われている
- 操作したのに結果が見えない・画面の外に出る（長い画面の最下部に出るエラー）
- 件数や状態の数え方の理由が画面から分からない（件数だけで、どれがなぜかが出ない）
- 左の一覧のハイライトなど、今どこにいるかの手がかりが合っているか
- 色だけで状態を伝えていないか（バッジは言葉も出るか）
- 初回の案内が、実データを見る邪魔にならないか

指摘は画像だけで済ませず、画面の文字列はソースで裏を取ってから書く。

```sh
cd "$BOARD" && grep -rn "<画面の文言の一部>" src docs | head
```

文言は `src/core/*.ts`（拡張ホストが組む文）、`src/webview/**/text.ts`・`*.tsx`（画面）、`src/webview/tours/*.yml`（案内）にある。
ccnavi 本体が返す文言（エラーの文、判定の結果）は `$CCNAVI/src/ccnavi/` にある。文言の出どころが拡張か実行ファイルかで、直す場所が変わる。
指摘には、画像の名前、画面と操作、ソースの場所（`ファイル:行`）、日本語の読み手がどこで迷うかを書く。

## 5. 今回確認できなかったこと（次回の宿題）

| 項目 | できなかった理由 | 試す案 |
|---|---|---|
| VS Code 本体の日本語表示 | 日本語言語パックを入れられなかった（Open VSX に出られない） | ネットワークの制限が無い環境で `ms-ceintl.vscode-language-pack-ja` を `--install-extension` する。入れてから `locale` を `ja` にして撮り直す。その環境で、メニューや通知が日本語のとき、拡張の英語混じりの表記がどう見えるかも見る |
| ライトテーマ・見た目の切り替え（◐ ボタン） | 設定に `Default Dark Modern` を書いた撮影は暗く、あとから `Default Light Modern` に書き換えても暗いままだった（原因は未調査）。設定に何も書かない 1.5 の手順では、明るい配色で撮れた。◐ ボタンは押していない | ユーザー設定の `workbench.colorTheme` で明るい・暗いを明示し、ブラウザ側の保存（`--user-data-dir` を作り直す）も消してから再読み込みし、左上の ◐（見た目を切り替えるコマンド）で `claude-light`・`claude-dark`・`vscode` を順に撮る。ハイコントラストも見る |
| 取り込み待ちの差分の展開 | 行の ▶ を押す操作が開かなかった（セレクタの特定に失敗した） | 開発者ツールで行の要素を調べてから押す。差分の見出し、追加行・削除行の色、長いファイルの扱いを撮る |
| レビュー待ちチケットの「レビュー完了」操作 | レビュー待ちの子（`wip/proposals/review/` に置かれた状態）を作っていない | `ccnavi-ticket.sh finish` で子を閉じる流れ（[manual-checks.md](manual-checks.md) の 5・5b）で状態を作る。指摘の対応方針を選ぶ画面と、指摘が無いときの画面を撮る |
| 実機の等幅フォント・ターミナル | code-server のため | 実機の VS Code で同じ画面を開き、「¥」とターミナルの崩れが出るか確かめる |

## 5b. 手動確認の表 25〜44 を code-server で回した結果

通った行: 25・25b の入力の保持・30（子が作業中のプロジェクトは情報色の帯で止まり、別のプロジェクトは保存できた）・25c・25d（失敗する実行ファイルで）・25e・26（clone の送信と成功）・27（資格情報と名前の重複）・28・29（保存と `--explain`）・33・35・36・37・38・39・40・41・42・43・44（`finish` は doing を done へ移して近似）。
通らなかった・確かめられなかった行: 25b の「表示中に一覧だけが増える」（増えなかった）・27 の同じ origin（名前の衝突の文が先に出ていた。拡張を直した）・34 の `project/risks.yml` の path（test-repo のコピーに無い。表 34 の期待は `common/risks.yml`）。上に書いていない行（25f〜25j・29c・29d・37b・37c・38b・38c・44b〜44d）は回していない。
気づいた点（実機で要確認）: clone の終了や外からの clone で、表示中の一覧が自動で増えない。行 29 の判定の id が `rules.yml:docs-guard` と出るのは不具合ではない（判定は編集中の内容の一時ファイルで行い、画面がそのパスを `rules.yml` に縮める設計。`src/core/rules-view.ts` の `tmpName`。`--explain` では `repo:docs-guard` と出る）。

## 6. 画像の置き場

リポジトリに画像を入れない。置き場は `$W/shots/` のような一時ディレクトリ（リポジトリの外）にする。
PR に付けるのは、指摘の根拠になる数枚だけにする。画像の名前（`<状況>-<番号>-<画面か操作>.png`）は、指摘の本文から指す。
