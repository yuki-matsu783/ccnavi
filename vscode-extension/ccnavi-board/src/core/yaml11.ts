/**
 * 拡張が書く YAML（`yaml` ライブラリ、YAML 1.2）を、実行ファイル（PyYAML、YAML 1.1）が別の型に
 * 読んでしまう語を見分ける。
 *
 * `yaml` は 1.2 の規則でしか引用符を足さないので、`yes` `on` `1:30` `0755` のような値を裸で書くと
 * PyYAML は真偽値・六十進・八進として読み、`message` が `True` になったり id が `True` を名乗ったりする。
 * `--lint` はそれを止めない（読めた値が正しい型なら苦情が無い）。ここに当たる文字は
 * 引用符で囲んで書く。当たらない文字を囲まないのは、変えていない行の見た目を保つため。
 *
 * 規則は PyYAML の resolver.py の実装（bool / null / int / float / timestamp / merge / value）。
 * 1.2 でも型になる語（`true` `null` `123` `1.5` `.inf`）は `yaml` が自分で囲むが、重ねて囲んでも害は無い。
 */
const PATTERNS: readonly RegExp[] = [
  /^(y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$/,
  /^(~|null|Null|NULL)$/,
  /^[-+]?0b[0-1_]+$/,
  /^[-+]?0[0-7_]+$/,
  /^[-+]?(0|[1-9][0-9_]*)$/,
  /^[-+]?0x[0-9a-fA-F_]+$/,
  /^[-+]?[1-9][0-9_]*(:[0-5]?[0-9])+$/,
  /^[-+]?([0-9][0-9_]*)?\.[0-9_]*([eE][-+][0-9]+)?$/,
  /^[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+\.[0-9_]*$/,
  /^[-+]?\.(inf|Inf|INF)$/,
  /^\.(nan|NaN|NAN)$/,
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/,
  /^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(\.[0-9]*)?(([ \t]*)(Z|[-+][0-9]{1,2}(:[0-9]{2})?))?$/,
  /^=$/,
  /^<<$/,
];

/** PyYAML（YAML 1.1）が文字列以外に読む綴りなら真。空文字は真（`null` に読まれる） */
export function yaml11Ambiguous(text: string): boolean {
  if (text === "") {
    return true;
  }
  return PATTERNS.some((p) => p.test(text));
}
