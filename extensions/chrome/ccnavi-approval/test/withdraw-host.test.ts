/**
 * 取り下げの判定を、ホストの応答の見本（test/fixtures/host/<github|gitlab>/withdraw-<場面>/）で通す。
 *
 * 承認は提案を中身を変えずに動かすので、取り下げは「承認済みチケットの中身が、承認コミットの親の提案と
 * バイト単位で同じ」で決める。比べるのは Python で、拡張はホストから読んだ本文を渡すだけ。ここでは
 * 承認コミットを引いて提案を読むところ（`findPrior`）と、親のブランチの先頭の承認済みチケットを読むところを
 * 実物と同じ関数で通し、ホストの応答の形（GitHub の GraphQL の `text`、GitLab の base64）から組んだ本文で、
 * 手で動かした承認（rename のコミット）と ccnavi の承認のどちらも判定が期待どおりになることを見る。
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import * as gh from "../src/core/github.js";
import * as gl from "../src/core/gitlab.js";
import type { Placement, PyCall } from "../src/core/py.js";
import { findPrior } from "../src/core/snapshot.js";
import { HOSTS } from "./helpers/host.js";
import { TOKEN } from "./helpers/mock-github.js";
import { HERE, nativePy, pyodidePy } from "./helpers/python.js";
import { loadWithdrawScene, withdrawSceneFetch, withdrawSceneNames, type WithdrawScene } from "./helpers/withdraw-fixture.js";

const APPROVED = ".ccnavi/approved";
const PLACE = { approved: APPROVED, tickets: "wip/proposals" } as unknown as Placement;
const noWait = async () => undefined;

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});
const native = nativePy();
after(() => native?.close());

/** 取り下げの見本の要求（手元の試験が書き出した、承認の直後の親子のチケット 1 組） */
function baseRequest(): Record<string, unknown> & { snapshot: { branches: Record<string, unknown> } } {
  const file = path.join(HERE, "test", "fixtures", "core-scenarios.json");
  return JSON.parse(fs.readFileSync(file, "utf8")).withdraw.request;
}

async function judged(scene: WithdrawScene) {
  const doing = `${APPROVED}/doing/${scene.ident}.md`;
  const workflow = `${APPROVED}/phases/${scene.ident}/workflow.yml`;
  const host = HOSTS.find((h) => h.id === (scene.host === "github" ? "github.com" : "gitlab.com"))!;
  const client = { host, token: TOKEN, fetch: withdrawSceneFetch(scene, doing), counter: { rest: 0, graphql: 0 }, sleep: noWait };
  const mod = scene.host === "github" ? gh : gl;
  const ask = async (op: string, args: readonly unknown[]) => {
    const fn = (mod as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>)[op];
    return await fn(client, scene.owner, scene.repo, ...args);
  };
  const prior = await findPrior(ask, PLACE, scene.head, scene.ident);
  assert.ok(prior !== null, `${scene.host}/${scene.name}: 承認コミットの親の提案を引けない`);
  // 親のブランチの先頭の承認済みチケットと待ち方。ボードと同じく、ホストの blob の本文から組む
  const objs = await mod.pathObjects(client, scene.owner, scene.repo, scene.head, [doing, workflow]);
  const oids = [doing, workflow].map((p) => objs[p]).filter((o) => o !== null && o !== undefined).map((o) => o!.oid);
  const texts = await mod.blobs(client, scene.owner, scene.repo, oids);
  const files: Record<string, string> = {};
  for (const p of [doing, workflow]) {
    const o = objs[p];
    if (o) files[p] = texts[o.oid].text as string;
  }
  const req = baseRequest();
  const request = {
    ...req,
    ids: [scene.ident],
    prior: { [scene.ident]: prior },
    snapshot: { ...req.snapshot, branches: { ...req.snapshot.branches, [scene.branch]: { head: scene.head, files } } },
  };
  return { request, prior, files, doing };
}

for (const [id, host] of [
  ["CX-T180", "github"],
  ["CX-T181", "gitlab"],
] as const) {
  test(`${id} ${host} の取り下げの見本（ccnavi の承認・手で動かした rename の承認）で、取り下げの判定が期待どおりになる`, async () => {
    assert.deepEqual(withdrawSceneNames(host), ["withdraw-agree", "withdraw-manual", "withdraw-manual-plan"]);
    for (const name of withdrawSceneNames(host)) {
      const scene = loadWithdrawScene(host, name);
      const { request, prior, files, doing } = await judged(scene);
      // 承認は中身を変えないので、承認済みチケットは提案と同じ本文（CRLF の提案も CRLF のまま）
      assert.equal(files[doing], prior, `${host}/${name}`);
      const expected = (scene.files["expected.json"] as { problems: string[] }).problems;
      const answer = (await py(request)) as { problems: string[]; changes: unknown };
      assert.deepEqual(answer.problems, expected, `${host}/${name} Pyodide`);
      assert.equal(answer.changes === null, expected.length > 0, `${host}/${name}`);
      if (native !== null) assert.deepEqual(await native.call(request), answer, `${host}/${name} CPython`);
    }
  });
}

test("CX-T182 取り下げの見本で、承認済みチケットの中身が 1 バイトでも違えば（改行を揃えただけでも）取り下げない", async () => {
  const scene = loadWithdrawScene("github", "withdraw-manual");
  const { request, prior } = await judged(scene);
  assert.match(prior, /\r\n/);
  const folded = { ...request, prior: { [scene.ident]: prior.replace(/\r\n/g, "\n") } };
  const answer = (await py(folded)) as { problems: string[] };
  assert.ok(answer.problems.some((p) => /中身が変わった/.test(p)), JSON.stringify(answer.problems));
});
