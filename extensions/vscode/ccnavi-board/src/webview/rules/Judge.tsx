/**
 * 「判定を試す」の結果。1 件の判定（`judged`）と、期待する判定の一括確認（`expectationsChecked`）、記録から起こした
 * ルールの候補（`suggested`）。
 *
 * ここで出すのは、実行ファイル（`--test --json` / `--test-expectations --json` /
 * `--suggest --json`）が返した形をそのまま読んだものだけ。
 * 候補もルールに足さない。下書きを見せるだけで、置くのはユーザ。
 */
import { Fragment, type JSX } from "react";

import type { HookEntry } from "../../core/hooks.js";
import { SECTIONS, tmpName } from "../../core/rules-view.js";
import type { SuggestJson } from "../../core/suggestmodel.js";
import { JUDGE_LABELS } from "./text.js";
import type { ExpectationsJson, RuleHitJson, TestJson } from "../../core/testmodel.js";

/** 判定のバッジ。空（判定の対象外）は薄い地の色で出す */
function Verdict({ verdict }: { readonly verdict: string }): JSX.Element {
  return <span className={`verdict ${verdict === "" ? "none" : verdict}`}>{verdict === "" ? "（判定の対象外）" : verdict}</span>;
}

/** 値のある組だけ出す定義リスト（`dl`） */
function Pairs({ pairs }: { readonly pairs: readonly (readonly [keyof typeof JUDGE_LABELS, string])[] }): JSX.Element {
  return (
    <dl className="kv">
      {pairs
        .filter(([, value]) => value !== "")
        .map(([name, value]) => (
          <Fragment key={name}>
            <dt>{JUDGE_LABELS[name]}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
    </dl>
  );
}

function Hits({ rules }: { readonly rules: readonly RuleHitJson[] }): JSX.Element {
  if (rules.length === 0) {
    return <p className="empty">どのルールにもヒットしませんでした</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>タイプ</th>
            <th>id</th>
            <th>指定したパターン</th>
            <th>正規表現に直した形</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule, index) => (
            <tr key={index}>
              <td>{rule.section !== "" && <span className={`verdict ${rule.section}`}>{rule.section}</span>}</td>
              <td>{tmpName(rule.id) + (rule.source === "outside" ? "（ルールファイル外の根拠）" : "")}</td>
              <td>{rule.kind !== "" && <code>{`${rule.kind} ${rule.written}`}</code>}</td>
              <td>{rule.pattern !== "" && <code>{rule.pattern}</code>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** そのツールで実行される hook。判定の結果と一緒に出す（拡張ホストが絞ってから渡す） */
function RunningHooks({ hooks }: { readonly hooks: readonly HookEntry[] }): JSX.Element {
  if (hooks.length === 0) {
    return <p className="empty">実行される hook はありません</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>イベント</th>
            <th>matcher</th>
            <th>コマンド</th>
          </tr>
        </thead>
        <tbody>
          {hooks.map((hook, index) => (
            <tr key={index}>
              <td>{hook.event}</td>
              <td>
                <code>{hook.matcher === "" ? "（全部）" : hook.matcher}</code>
              </td>
              <td className="cmd">
                <code>{hook.command}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface Judged {
  readonly result: TestJson;
  readonly hooks: readonly HookEntry[];
}

export function JudgeResult({ judged }: { readonly judged: Judged }): JSX.Element {
  const { result } = judged;
  return (
    <div id="judge-result" className="result" data-tour="judge-result">
      {result.known ? (
        <>
          <p>
            <Verdict verdict={result.verdict} />
            {result.code !== "" && ` ${result.code}`}
          </p>
          <Pairs
            pairs={[
              ["tool", result.tool],
              ["subject", result.subject],
              ["resolved", result.resolved],
              ["reason", tmpName(result.reason)],
              ["degraded", result.degraded === "" ? "" : `${result.degraded}（生の文字列に対して判定）`],
              ["fallback", result.fallback === "" ? "" : `${result.fallback}（組み込みの既定で判定）`],
            ]}
          />
          <h3>ヒットしたルール</h3>
          <Hits rules={result.rules} />
          <h3>返すメッセージ</h3>
          {result.response === "" ? <p className="empty">メッセージは返しません</p> : <pre className="response">{tmpName(result.response)}</pre>}
        </>
      ) : (
        <p>
          <Verdict verdict="" /> {result.tool} は判定の対象を取り出せないツールです。ルールを作成してもヒットせず、呼び出しはそのまま通ります
        </p>
      )}
      <h3>このツールで実行される hook</h3>
      <RunningHooks hooks={judged.hooks} />
    </div>
  );
}

export function ExpectationsResult({ result }: { readonly result: ExpectationsJson }): JSX.Element {
  return (
    <div id="expectations-result" className="result" data-tour="expectations-result">
      <div className="counts" data-tour="expectations-counts">
        {SECTIONS.map((section) => {
          const count = result.counts[section] ?? { ok: 0, total: 0 };
          return (
            <span key={section} className={count.ok === count.total ? "" : "ng"}>
              {section} {count.ok}/{count.total}
            </span>
          );
        })}
        <span className={result.mismatches > 0 ? "ng" : ""}>不一致 {result.mismatches} 件</span>
        <span>判定の対象外 {result.skipped} 件</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>期待</th>
              <th>判定</th>
              <th>ツール</th>
              <th>対象</th>
              <th>ヒットしたルール</th>
              <th>理由</th>
            </tr>
          </thead>
          <tbody>
            {result.expectations.map((expectation, index) => (
              <tr
                key={index}
                className={expectation.ok ? (expectation.skipped ? "skipped" : "") : "ng"}
                data-tour={!expectation.ok && result.expectations.findIndex((item) => !item.ok) === index ? "expectation-ng" : undefined}
              >
                <td>
                  <span className={`verdict ${expectation.expected}`}>{expectation.expected}</span>
                </td>
                <td>
                  <Verdict verdict={expectation.known ? expectation.verdict : ""} />
                </td>
                <td>{expectation.tool}</td>
                <td className="cmd">
                  <code>{expectation.subject}</code>
                </td>
                <td>
                  {expectation.rules
                    .filter((rule) => rule.source === "file")
                    .map((rule) => `${rule.section}:${tmpName(rule.id)}`)
                    .join(", ")}
                </td>
                <td className="why">{tmpName(expectation.why)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 候補の種類の言い換え */
const SUGGEST_KINDS: Readonly<Record<string, string>> = {
  rule: "ルールを追加する",
  message: "message を見直す",
};

export function SuggestResult({ result }: { readonly result: SuggestJson }): JSX.Element {
  return (
    <div id="suggest-result" className="result" data-tour="suggest-result">
      <p>
        記録 {result.logs.length} 本・{result.records} 行から、候補 {result.candidates.length} 件
        （検証を通らず除いたもの {result.dropped} 件）。候補はすべて下書きです。確認して設定に反映するのはユーザです。
      </p>
      {result.candidates.length === 0 ? (
        <p className="empty">候補はありません</p>
      ) : (
        result.candidates.map((candidate, index) => (
          <div key={index} className="candidate" data-id={candidate.id} data-tour={index === 0 ? "suggest-candidate" : undefined}>
            <p>
              <span className={`verdict ${candidate.section}`}>{candidate.section}</span>{" "}
              {SUGGEST_KINDS[candidate.kind] ?? candidate.kind}: <code>{candidate.id}</code>
            </p>
            <p>{candidate.summary}</p>
            <pre className="response">{candidate.yaml}</pre>
          </div>
        ))
      )}
    </div>
  );
}
