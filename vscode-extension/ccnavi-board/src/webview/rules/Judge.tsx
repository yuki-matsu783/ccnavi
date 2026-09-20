/**
 * 「判定を試す」の結果。1 件の判定（`judged`）と、サンプルの一括判定（`sampled`）。
 *
 * **ここは判定しない。** 出すのは実行ファイル（`--test --json` / `--test-samples --json`）が
 * 返した形をそのまま読んだものだけで、当たる・当たらないの理屈は持たない。
 */
import { Fragment, type JSX } from "react";

import type { HookEntry } from "../../core/hooks.js";
import { SECTIONS } from "../../core/rules-view.js";
import type { RuleHitJson, SamplesJson, TestJson } from "../../core/testmodel.js";

/** 判定の札。空（判定の対象外）は薄い地の色で出す */
export function Verdict({ verdict }: { readonly verdict: string }): JSX.Element {
  return <span className={`verdict ${verdict === "" ? "none" : verdict}`}>{verdict === "" ? "（判定の対象外）" : verdict}</span>;
}

/** 値のある組だけ出す定義リスト（`dl`） */
function Pairs({ pairs }: { readonly pairs: readonly (readonly [string, string])[] }): JSX.Element {
  return (
    <dl className="kv">
      {pairs
        .filter(([, value]) => value !== "")
        .map(([name, value]) => (
          <Fragment key={name}>
            <dt>{name}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
    </dl>
  );
}

function Hits({ rules }: { readonly rules: readonly RuleHitJson[] }): JSX.Element {
  if (rules.length === 0) {
    return <p className="empty">どのルールにもヒットしなかった</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>タイプ</th>
          <th>id</th>
          <th>書いたパターン</th>
          <th>正規表現に直した形</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((rule, index) => (
          <tr key={index}>
            <td>{rule.section !== "" && <span className={`verdict ${rule.section}`}>{rule.section}</span>}</td>
            <td>{rule.id + (rule.source === "outside" ? "（ルールファイル外の根拠）" : "")}</td>
            <td>{rule.kind !== "" && <code>{`${rule.kind} ${rule.written}`}</code>}</td>
            <td>{rule.pattern !== "" && <code>{rule.pattern}</code>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** そのツールで実行される hook。判定の結果と一緒に出す（拡張ホストが絞ってから渡す） */
function RunningHooks({ hooks }: { readonly hooks: readonly HookEntry[] }): JSX.Element {
  if (hooks.length === 0) {
    return <p className="empty">実行される hook は無い</p>;
  }
  return (
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
  );
}

export interface Judged {
  readonly result: TestJson;
  readonly hooks: readonly HookEntry[];
}

export function JudgeResult({ judged }: { readonly judged: Judged }): JSX.Element {
  const { result } = judged;
  return (
    <div id="judge-result" className="result">
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
              ["reason", result.reason],
              ["degraded", result.degraded === "" ? "" : `${result.degraded}（生の文字列に対して判定）`],
              ["fallback", result.fallback === "" ? "" : `${result.fallback}（組み込みの既定で判定）`],
            ]}
          />
          <h3>ヒットしたルール</h3>
          <Hits rules={result.rules} />
          <h3>返すメッセージ</h3>
          {result.response === "" ? <p className="empty">メッセージは返さない</p> : <pre className="response">{result.response}</pre>}
        </>
      ) : (
        <p>
          <Verdict verdict="" /> {result.tool} は判定の対象を取り出せないツール。ルールを書いてもヒットせず、呼び出しはそのまま通る
        </p>
      )}
      <h3>このツールで実行される hook</h3>
      <RunningHooks hooks={judged.hooks} />
    </div>
  );
}

export function SamplesResult({ result }: { readonly result: SamplesJson }): JSX.Element {
  return (
    <div id="samples-result" className="result">
      <div className="counts">
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
          {result.samples.map((sample, index) => (
            <tr key={index} className={sample.ok ? (sample.skipped ? "skipped" : "") : "ng"}>
              <td>
                <span className={`verdict ${sample.expected}`}>{sample.expected}</span>
              </td>
              <td>
                <Verdict verdict={sample.known ? sample.verdict : ""} />
              </td>
              <td>{sample.tool}</td>
              <td className="cmd">
                <code>{sample.subject}</code>
              </td>
              <td>
                {sample.rules
                  .filter((rule) => rule.source === "file")
                  .map((rule) => `${rule.section}:${rule.id}`)
                  .join(", ")}
              </td>
              <td>{sample.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
