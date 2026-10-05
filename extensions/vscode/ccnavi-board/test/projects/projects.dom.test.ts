/**
 * プロジェクト管理画面（React）を happy-dom で動かす。clone と .gitignore のボタンの送り先、
 * カードに出るレイヤーの置き場と苦情、チケット制御が disable のときの件数の欄。
 *
 * 移す前は拡張ホストが組んだ HTML の文字列を正規表現で見ていた（CB-T113 / CB-T123 / CB-T133）。
 * 確かめている中身はそのままで、見る先を DOM に移してある。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { cardSelector, openProjects, page, problem, row } from "../helpers/projects.js";
import type { HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は落とす */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

test("CB-D31 .gitignore の帯のボタンと clone は、それぞれの型で送る。clone は欄の URL と名前を送り、名前は URL から埋まる", async () => {
  const dom = await openProjects([row()], { ignored: false });
  try {
    dom.click(dom.one('button[data-action="fix-ignore"]'));
    await dom.settle();
    dom.type(dom.one("#url"), "https://gitlab.example.com/g/tool.git");
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>("#name").value, "tool");
    dom.click(dom.one('button[data-action="clone"]'));
    await dom.settle();
    assert.deepEqual(dom.posted, [
      { type: "ready" },
      { type: "fixIgnore" },
      { type: "clone", url: "https://gitlab.example.com/g/tool.git", name: "tool" },
    ]);
    // 打ちかけは Webview の state に残す。作り直されても残る
    assert.deepEqual(dom.state(), { url: "https://gitlab.example.com/g/tool.git", name: "tool", nameTouched: false });
    await dom.send({ type: "cloned", message: "clone を送った" });
    assert.equal(dom.one<HTMLInputElement>("#url").value, "");
    assert.equal(text(dom.one("#status")), "clone を送った");
  } finally {
    await dom.close();
  }
});

test("CB-D32 中身は postMessage で入れ替わり、読み直せなければ文面を出す。見た目は body のクラスだけ変える", async () => {
  const dom = await openProjects();
  try {
    await dom.send({ type: "data", data: { kind: "page", page: page([row({ name: "app", rel: "projects/app" })]) } });
    assert.deepEqual(
      dom.all("li.project").map((li) => li.getAttribute("data-name")),
      ["app"],
    );
    await dom.send({ type: "appearance", value: "claude-dark" });
    assert.ok(dom.document.body.classList.contains("ccnavi-claude-dark"));
    await dom.send({ type: "appearance", value: "vscode" });
    assert.ok(!dom.document.body.classList.contains("ccnavi-claude-dark"));
    await dom.send({ type: "data", data: { kind: "error", error: "実行ファイルが返らない" } });
    assert.equal(dom.all("li.project").length, 0);
    assert.equal(text(dom.one("pre.load-error")), "実行ファイルが返らない");
  } finally {
    await dom.close();
  }
});

test("CB-D34 失敗の一言は次の一覧が届いたら消える。案内は残る", async () => {
  const dom = await openProjects();
  try {
    const status = () => text(dom.one("#status"));
    const failed = () => dom.one("#status").className.includes("failed");
    await dom.send({ type: "failed", message: "プロジェクト lib が一覧に無い。更新してから押し直す" });
    assert.match(status(), /一覧に無い/);
    assert.ok(failed());
    // 一覧が新しくなったら、それを見て言った失敗はもう今のことではない
    await dom.send({ type: "data", data: { kind: "page", page: page([row()]) } });
    assert.equal(status(), "");
    assert.ok(!failed());
    // 案内は残す。clone は .git の出現で必ず読み直しが走るので、ここで消すと一瞬で消える
    await dom.send({ type: "cloned", message: "clone をターミナルに送った" });
    await dom.send({ type: "data", data: { kind: "page", page: page([row(), row({ name: "app", rel: "projects/app" })]) } });
    assert.equal(status(), "clone をターミナルに送った");
  } finally {
    await dom.close();
  }
});

test("CB-T113 カードはレイヤーの置き場を出す。自身のレイヤーは本体の枠に出す。操作のボタンは無い", async () => {
  const dom = await openProjects([
    row({ name: "app", rel: "projects/app", rulesRel: "projects/app/.ccnavi/config/rules.yml" }),
    row({ rulesExists: false }),
    row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false }),
  ]);
  try {
    const rules = (name: string): string => text(dom.one(`${cardSelector(name)} .field:nth-child(2) dd`));
    assert.equal(rules("app"), "あり projects/app/.ccnavi/config/rules.yml");
    assert.equal(rules("lib"), "なし projects/lib/.ccnavi/config/rules.yml");
    // 予約名のプロジェクトはレイヤーが無いので、置く先も出さない
    assert.match(rules("Self"), /設定の対象になっていません/);
    assert.equal(dom.all("section.list button").length, 0, "カードに操作のボタンがある");

    assert.match(text(dom.one("section.workspace")), /ワークスペースの設定のルール なし \.ccnavi\/config\/rules\.yml$/);
    assert.equal(dom.all("section.workspace button").length, 0);
  } finally {
    await dom.close();
  }

  const exists = await openProjects([], { selfRulesExists: true });
  try {
    assert.match(text(exists.one("section.workspace")), /ワークスペースの設定のルール あり \.ccnavi\/config\/rules\.yml$/);
  } finally {
    await exists.close();
  }
});

test("CB-T123 プロジェクト管理は同じ事象の注意を 1 か所にだけ出す", async () => {
  const dom = await openProjects(
    [
      row({
        hasClaudeDir: true,
        problems: [
          problem("warn", ".claude/ を持つ。Claude Code がそこのスキルを読み込み、そこへ cd するだけで別のルートのように見える"),
          problem("warn", ".claude/settings.json を読めない: 壊れている"),
          problem("error", "文面が無い", "(projects/lib) x"),
        ],
      }),
    ],
    {
      ignored: false,
      dirProblems: [
        problem("warn", "projects/ がワークスペースの git で無視されていない", "(projects)"),
        problem("warn", "別の指摘", "(projects)"),
      ],
    },
  );
  try {
    const banners = dom.all(".banner").map((b) => text(b));
    // .gitignore の帯（直すボタン付き）があるので、lint の「無視されていない」は重ねない。別の指摘は出る
    assert.equal(dom.all('button[data-action="fix-ignore"]').length, 1);
    assert.ok(!banners.some((b) => /無視されていない/.test(b)), banners.join(" / "));
    assert.ok(banners.some((b) => b === "warn: 別の指摘"), banners.join(" / "));
    // .claude/ の説明があるので、lint の同じ指摘（実物の文面「.claude/ を持つ。…」）は重ねない。
    // ".claude/settings.json を読めない" のような別の warn と error は出る
    const lint = dom.all(`${cardSelector("lib")} ul.lint li`).map((li) => text(li));
    assert.ok(!lint.some((l) => /\.claude\/ を持つ/.test(l)), lint.join(" / "));
    assert.ok(lint.some((l) => l === "warn: .claude/settings.json を読めない: 壊れている"), lint.join(" / "));
    assert.ok(lint.some((l) => /\.claude\/ があります。Claude Code は.*プロジェクトの設定は \.ccnavi\/config\/ に置いてください/.test(l)), lint.join(" / "));
    assert.ok(lint.some((l) => l === "error: 文面が無い"), lint.join(" / "));
  } finally {
    await dom.close();
  }

  // 置き場の案内はレイヤーのルールの置き場から逆算する。ディレクトリを挟まない形やレイヤーでない行は既定
  const flat = await openProjects([
    row({ hasClaudeDir: true, rulesRel: "projects/lib/rules.yml" }),
    row({ name: "app", rel: "projects/app", hasClaudeDir: true, rulesRel: "projects/app/conf/ccnavi/rules.yml" }),
    row({ name: "Self", rel: "projects/Self", hasClaudeDir: true, rulesRel: "" }),
  ]);
  try {
    const dirs = flat.all("ul.lint li").flatMap((li) => [...text(li).matchAll(/プロジェクトの設定は ([^ ]+)\/ に置いてください/g)].map((m) => m[1]));
    assert.deepEqual(dirs, [".ccnavi/config", "conf/ccnavi", ".ccnavi/config"]);
  } finally {
    await flat.close();
  }

  // .gitignore が済んでいれば lint の指摘はそのまま出る
  const fine = await openProjects([row()], { dirProblems: [problem("warn", "projects/ がワークスペースの git で無視されていない", "(projects)")] });
  try {
    assert.ok(fine.all(".banner").some((b) => text(b) === "warn: projects/ がワークスペースの git で無視されていない"));
  } finally {
    await fine.close();
  }
});

// `projects/` がワークスペースの git の索引に載っているときの苦情（src/ccnavi/entry/lint.py の `_in_index`）。
// 先頭の句は 2 つで共通で、載せ忘れは直後に「（入れ子のリポジトリとして」が続く（設計 §4.2）
const COLLISION =
  "`projects/` はワークスペースの git が追跡している（例: `projects/foo/main.py`）。ccnavi はワークスペース直下の `projects/` をプロジェクトの置き場として使い、名前は変えられない。直すには、ワークスペースの `projects/` を別の名前に移す（例: `git mv projects apps`）";
const ADDED_BY_MISTAKE =
  "`projects/` はワークスペースの git が追跡している（入れ子のリポジトリとして: `projects/lib`）。直すには、ワークスペースで `git rm -r --cached projects` を打ち、`.gitignore` に `/projects/` を足して、コミットする";

/** 置き場の苦情の帯・`.gitignore` の帯とボタンを見る */
async function trackedBanners(detail: string, ignored: boolean): Promise<{ banners: string[]; fixButtons: number }> {
  const dom = await openProjects([row()], { ignored, dirProblems: [problem("warn", detail, "(projects)"), problem("warn", "別の指摘", "(projects)")] });
  try {
    return { banners: dom.all(".banner").map((b) => text(b)), fixButtons: dom.all('button[data-action="fix-ignore"]').length };
  } finally {
    await dom.close();
  }
}

test("CB-D148 A10 置き場がワークスペースのソースとぶつかっていたら、苦情の帯だけを出し、.gitignore のボタンと「無視されていない」の帯を出さない", async () => {
  const { banners, fixButtons } = await trackedBanners(COLLISION, false);
  assert.equal(fixButtons, 0);
  assert.ok(banners.includes(`warn: ${COLLISION}`), banners.join(" / "));
  assert.ok(!banners.some((b) => /\.gitignore.*が無い/.test(b)), banners.join(" / "));
  assert.ok(!banners.some((b) => /無視されていない/.test(b)), banners.join(" / "));
  // 別の指摘は今どおり出る
  assert.ok(banners.includes("warn: 別の指摘"), banners.join(" / "));
});

test("CB-D149 A10b 載せ忘れ（入れ子のリポジトリが索引に載った）でも同じ。.gitignore に /projects/ が既にあっても苦情の帯は出たまま", async () => {
  for (const ignored of [false, true]) {
    const { banners, fixButtons } = await trackedBanners(ADDED_BY_MISTAKE, ignored);
    assert.equal(fixButtons, 0, `ignored=${ignored}`);
    assert.ok(banners.includes(`warn: ${ADDED_BY_MISTAKE}`), `ignored=${ignored}: ${banners.join(" / ")}`);
    assert.ok(!banners.some((b) => /\.gitignore.*が無い/.test(b)), banners.join(" / "));
    assert.ok(!banners.some((b) => /無視されていない/.test(b)), banners.join(" / "));
  }
});

test("CB-T133 チケット制御が disable なら、チケットの件数の欄を出さない", async () => {
  const enabled = await openProjects([row({ tickets: 3 })]);
  try {
    assert.match(text(enabled.one(cardSelector("lib"))), /チケット\s*3 件/);
  } finally {
    await enabled.close();
  }

  const off = await openProjects([row({ tickets: 3 })], { ticketsEnabled: false });
  try {
    assert.ok(!/チケット/.test(text(off.one(cardSelector("lib")))));
  } finally {
    await off.close();
  }
});
