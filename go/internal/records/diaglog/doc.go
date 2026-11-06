// Package diaglog は診断ログ。`<ワークスペースルート>/logs/diag/<出どころ>.log` に 1 行ずつ足す
// （.agents/docs/logging.md）。sh（ccnavi-common-log.sh）と拡張（src/log.ts）と 1 字も違わない行を出す。
//
// 標準出力にも標準エラーにも出さず、書けない（置き場が作れない・権限・容量・リンク）ときは黙って破棄する。
// ログの成否で本体の判定・出力・終了コードを変えない。本文と値は書く前に MaskUserinfo（3 つの言語で
// 共通）と redact を通す。秘密の値をそもそも渡さないのが先で、これは最後の安全網。
//
// Python からの対応: get → Get、Logger の debug・info・warn・error → Debug・Info・Warn・Error（値は
// key, value の組を並べる）、enabled → Enabled、DIAG_DIR → Dir（ディスクのパスは DirOf）、mask_userinfo → MaskUserinfo。
// threshold・stamp・text・fold・quote・format_line・append は私的にした。
package diaglog
