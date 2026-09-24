/**
 * プロジェクト管理画面（React）を happy-dom で動かす。メニューの開閉、各ボタンの送り先、
 * カードに出る層の置き場と苦情、チケット制御が disable のときの入口。
 *
 * 移す前は拡張ホストが組んだ HTML の文字列を正規表現で見ていた（CB-T113 / CB-T123 / CB-T133）。
 * 確かめている中身はそのままで、見る先を DOM に移してある。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { cardSelector, openProjects, page, problem, projectsHtml, row } from "../helpers/projects.js";
import { flatStyle } from "../helpers/bundle.js";
import type { HTMLDetailsElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は落とす */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

test("CB-D30 メニューは 1 つだけ開き、項目を押すと名前付きで送って閉じる。Esc でも閉じる", async () => {
  const dom = await openProjects([row(), row({ name: "app", rel: "projects/app", root: "/ws/projects/app" })]);
  try {
    const menus = dom.all<HTMLDetailsElement>(`${cardSelector("lib")} details.menu`);
    assert.equal(menus.length, 2);
    dom.click(menus[0].querySelector("summary")!);
    await dom.settle();
    assert.ok(menus[0].open);
    dom.click(menus[1].querySelector("summary")!);
    await dom.settle();
    assert.ok(!menus[0].open, "別のメニューを開くと前のは閉じる");
    assert.ok(menus[1].open);
    dom.click(dom.one(`${cardSelector("lib")} button[data-action="pull"]`));
    await dom.settle();
    assert.deepEqual(dom.posted, [{ type: "ready" }, { type: "pull", name: "lib" }]);
    assert.ok(!menus[1].open, "項目を押すと閉じる");
    dom.click(menus[0].querySelector("summary")!);
    await dom.settle();
    dom.key("Escape");
    await dom.settle();
    assert.ok(!menus[0].open);
  } finally {
    await dom.close();
  }
});

test("CB-D31 帯と行のボタンはそれぞれの型で送る。clone は欄の URL と名前を送り、名前は URL から埋まる", async () => {
  const dom = await openProjects([row({ rulesExists: false })], { ignored: false });
  try {
    dom.click(dom.one('button[data-action="fix-ignore"]'));
    dom.click(dom.one('button[data-action="create-rules"][data-name="lib"]'));
    dom.click(dom.one('button[data-action="open-board"][data-name="lib"]'));
    await dom.settle();
    dom.type(dom.one("#url"), "https://gitlab.example.com/g/tool.git");
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>("#name").value, "tool");
    dom.click(dom.one('button[data-action="clone"]'));
    await dom.settle();
    assert.deepEqual(dom.posted, [
      { type: "ready" },
      { type: "fixIgnore" },
      { type: "createRules", name: "lib" },
      { type: "openBoard", name: "lib" },
      { type: "clone", url: "https://gitlab.example.com/g/tool.git", name: "tool" },
    ]);
    // 打ちかけは Webview の state に控える。作り直されても残る
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

test("CB-D33 開いていたメニューは、その行が一覧から消えたら閉じる", async () => {
  const dom = await openProjects([row(), row({ name: "app", rel: "projects/app" })]);
  try {
    const summary = (name: string) => dom.one(`${cardSelector(name)} details.menu summary`);
    dom.click(summary("lib"));
    await dom.settle();
    assert.ok(dom.one<HTMLDetailsElement>(`${cardSelector("lib")} details.menu`).open);
    // lib が消えて app だけになる。app のメニューは開かない
    await dom.send({ type: "data", data: { kind: "page", page: page([row({ name: "app", rel: "projects/app" })]) } });
    assert.ok(!dom.one<HTMLDetailsElement>(`${cardSelector("app")} details.menu`).open);
    // 同じ名前で戻ってきても、押していないメニューは閉じたまま
    await dom.send({ type: "data", data: { kind: "page", page: page([row(), row({ name: "app", rel: "projects/app" })]) } });
    assert.ok(!dom.one<HTMLDetailsElement>(`${cardSelector("lib")} details.menu`).open);
    // 残っている行のメニューは、読み直しても開いたまま（打ちかけと同じ扱い）
    dom.click(summary("app"));
    await dom.settle();
    await dom.send({ type: "data", data: { kind: "page", page: page([row(), row({ name: "app", rel: "projects/app" })]) } });
    assert.ok(dom.one<HTMLDetailsElement>(`${cardSelector("app")} details.menu`).open);
  } finally {
    await dom.close();
  }

  // 名前は置き場のディレクトリ名そのままで、clone の欄が通す綴りとは限らない。
  // `a` が `a:x` のメニューを自分のものだと言い出さないこと（前方一致だと言い出す）
  const colon = await openProjects([row({ name: "a", rel: "projects/a" }), row({ name: "a:x", rel: "projects/a:x" })]);
  try {
    colon.click(colon.one(`${cardSelector("a:x")} details.menu summary`));
    await colon.settle();
    assert.ok(colon.one<HTMLDetailsElement>(`${cardSelector("a:x")} details.menu`).open);
    // a:x が消えて a だけになる。戻ってきたとき、押していないメニューが開いていてはいけない
    await colon.send({ type: "data", data: { kind: "page", page: page([row({ name: "a", rel: "projects/a" })]) } });
    await colon.send({
      type: "data",
      data: { kind: "page", page: page([row({ name: "a", rel: "projects/a" }), row({ name: "a:x", rel: "projects/a:x" })]) },
    });
    assert.ok(!colon.one<HTMLDetailsElement>(`${cardSelector("a:x")} details.menu`).open);
  } finally {
    await colon.close();
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

test("CB-T113 カードは層の置き場を出す。自身の層は本体の枠に出す", async () => {
  const dom = await openProjects([
    row({ name: "app", rel: "projects/app", rulesRel: "projects/app/.ccnavi/config/rules.yml" }),
    row({ rulesExists: false }),
    row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false }),
  ]);
  try {
    const rules = (name: string): string => text(dom.one(`${cardSelector(name)} .field:nth-child(2) dd`));
    assert.equal(rules("app"), "あり projects/app/.ccnavi/config/rules.yml");
    assert.equal(rules("lib"), "なし projects/lib/.ccnavi/config/rules.yml 共通層からコピー");
    assert.equal(dom.all(`${cardSelector("lib")} button[data-action="create-rules"][data-name="lib"]`).length, 1);
    assert.ok(dom.one<HTMLInputElement>(`${cardSelector("lib")} button[data-action="open-rules"]`).hasAttribute("disabled"));
    // 予約名のプロジェクトは層が無いので、置く先も作るボタンも出さない
    assert.match(rules("Self"), /層として数えられていない/);
    assert.equal(dom.all(`${cardSelector("Self")} button[data-action="create-rules"]`).length, 0);

    const workspace = dom.one("section.workspace");
    assert.match(text(workspace), /自身の層のルール なし \.ccnavi\/config\/rules\.yml 共通層からコピー ルール設定/);
    assert.equal(dom.all('button[data-action="create-self-rules"]').length, 1);
    assert.ok(dom.one('button[data-action="open-self-rules"]').hasAttribute("disabled"));
  } finally {
    await dom.close();
  }

  const exists = await openProjects([], { selfRulesExists: true });
  try {
    assert.equal(exists.all('button[data-action="create-self-rules"]').length, 0);
    assert.ok(!exists.one('button[data-action="open-self-rules"]').hasAttribute("disabled"));
  } finally {
    await exists.close();
  }
});

test("CB-T123 プロジェクト管理は同じ事象の注意を 1 か所にだけ出し、行末のボタンは 2 つのメニューにまとめる", async () => {
  const dom = await openProjects(
    [
      row({
        hasClaudeDir: true,
        problems: [
          problem("warn", ".claude/ を持つ。Claude Code がそこのスキルを読み、cd 1 回で別のルートに見える"),
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
    assert.ok(lint.some((l) => /\.claude\/ がある。Claude Code は.*プロジェクトの設定は \.ccnavi\/config\/ に置く/.test(l)), lint.join(" / "));
    assert.ok(lint.some((l) => l === "error: 文面が無い"), lint.join(" / "));
    // 行末は「開く ▾」と「git ▾」の 2 つ。中のボタンの data-action は前のまま
    const menus = dom.all(`${cardSelector("lib")} details.menu`);
    assert.equal(menus.length, 2);
    assert.deepEqual(menus.map((m) => text(m.querySelector("summary"))), ["開く ▾", "git ▾"]);
    for (const action of ["open-rules", "open-phases", "open-board", "fetch", "pull"]) {
      assert.equal(dom.all(`${cardSelector("lib")} button[data-action="${action}"][data-name="lib"]`).length, 1, action);
    }
  } finally {
    await dom.close();
  }

  // 置き場の案内は層のルールの置き場から逆算する。ディレクトリを挟まない形や層でない行は既定
  const flat = await openProjects([
    row({ hasClaudeDir: true, rulesRel: "projects/lib/rules.yml" }),
    row({ name: "app", rel: "projects/app", hasClaudeDir: true, rulesRel: "projects/app/conf/ccnavi/rules.yml" }),
    row({ name: "Self", rel: "projects/Self", hasClaudeDir: true, rulesRel: "" }),
  ]);
  try {
    const dirs = flat.all("ul.lint li").flatMap((li) => [...text(li).matchAll(/プロジェクトの設定は ([^ ]+)\/ に置く/g)].map((m) => m[1]));
    assert.deepEqual(dirs, [".ccnavi/config", "conf/ccnavi", ".ccnavi/config"]);
  } finally {
    await flat.close();
  }

  // メニューの項目は HC で枠が出る書き方（contrastBorder の変数）。押せない項目は点線
  const css = flatStyle(projectsHtml({ kind: "page", page: page([row()]) }));
  assert.match(css, /\.menu > \.menu-items > button\.action \{ justify-content: flex-start; border-color: var\(--vscode-contrastBorder, transparent\);/);
  assert.match(css, /\.menu > \.menu-items > button\.action:disabled \{ border-style: dashed; \}/);

  // .gitignore が済んでいれば lint の指摘はそのまま出る
  const fine = await openProjects([row()], { dirProblems: [problem("warn", "projects/ がワークスペースの git で無視されていない", "(projects)")] });
  try {
    assert.ok(fine.all(".banner").some((b) => text(b) === "warn: projects/ がワークスペースの git で無視されていない"));
  } finally {
    await fine.close();
  }
});

test("CB-T133 チケット制御が disable なら、チケット管理とフェーズ管理の入口を出さない", async () => {
  const enabled = await openProjects([row()]);
  try {
    for (const action of ["open-board", "open-phases", "open-self-phases"]) {
      assert.ok(enabled.all(`button[data-action="${action}"]`).length > 0, action);
    }
  } finally {
    await enabled.close();
  }

  // 配点とフェーズの種類はチケットにしか読まれない。disable の間は入口ごと消す
  const off = await openProjects([row()], { ticketsEnabled: false });
  try {
    for (const action of ["open-board", "open-phases", "open-self-phases"]) {
      assert.equal(off.all(`button[data-action="${action}"]`).length, 0, action);
    }
    const body = text(off.document.body);
    assert.ok(!/自身の層のフェーズの種類/.test(body));
    assert.ok(!/フェーズ管理/.test(body));
    assert.ok(!/チケット管理/.test(body));
    // ルールとプロジェクトの操作は disable でも残る。「開く ▾」の中はルール設定だけになる
    for (const action of ["open-rules", "fetch", "pull"]) {
      assert.equal(off.all(`${cardSelector("lib")} button[data-action="${action}"][data-name="lib"]`).length, 1, action);
    }
    assert.deepEqual(
      off.all(`${cardSelector("lib")} button[data-action^="open-"]`).map((b) => b.getAttribute("data-action")),
      ["open-rules"],
    );
    assert.equal(off.all('button[data-action="open-self-rules"]').length, 1);
    assert.match(text(off.one("section.workspace")), /自身の層のルール/);
    // チケットの件数の欄も出さない
    assert.ok(!/チケット/.test(text(off.one(cardSelector("lib")))));
  } finally {
    await off.close();
  }
});

test("CB-D98 プロジェクトが無い画面では、案内の間だけ見本の行を出し、閉じたら消して tourDone を返す", async () => {
  const dom = await openProjects([]);
  try {
    assert.equal(dom.all("li.project").length, 0);
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour-sample").length, 1, "見本だと分かる帯が無い");
    assert.equal(dom.all('li.project[data-name="sample-app"]').length, 1);
    const titles: string[] = [];
    while (dom.all(".tour").length > 0) {
      titles.push(dom.one("#tour-title").textContent ?? "");
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.deepEqual(titles, ["clone する", "プロジェクト", "ワークスペース自身", "共通層のルール", "案内"]);
    assert.equal(dom.all("li.project").length, 0, "閉じたのに見本が残った");
    assert.equal(dom.all(".tour-sample").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D99 プロジェクトがある画面では見本を出さず、「？ 案内」から案内を出せる", async () => {
  const dom = await openProjects();
  try {
    // 案内の入口は ? 1 文字で、ヘッダ（ツールバー）の最後の子。位置は Tour.css が 5 画面とも右上に揃える
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "clone する");
    assert.equal(dom.all(".tour-sample").length, 0);
    assert.equal(dom.all("li.project").length, 1);
  } finally {
    await dom.close();
  }
});
