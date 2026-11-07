package lint

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/stopreview"
)

// Claude Code の設定ファイル（`.claude/settings.json` と `.claude/settings.local.json`）の検査。ccnavi は環境変数しか
// 読まないが、ここに書かれた env は Claude Code がプロセスに渡すので、エージェントが書き換えられるファイルに防御を
// 無効化する値が無いかを確かめられるのはここだけになる。

// 文に出す設定ファイルの表記（ワークスペースルートからの相対）。
var (
	projectSettingsRel = config.SharedClaudeSettings
	localSettingsRel   = config.LocalClaudeSettings
)

// stopReviewPromptShown は Stop hook の prompt の数えた分のうち持っておく長さ（文字）。
const stopReviewPromptShown = 60

// localForbidden は `.claude/settings.local.json` に置かせない、承認と判定に影響する値。手元だけで切ると、
// ターミナルの sh・Chrome・他の機械で、同じ親子のチケットに掛かる保護が別になる。判定の答えを変えない値（実行ファイルの
// パス・診断ログ・待ちの秒）は入れない。
var localForbidden = []string{
	env.TicketControl,
	env.Learning,
	env.GuardTicketApproval,
	env.GuardCoreFiles,
	env.GuardUnwatched,
	env.RestoreIfDeny,
	env.DenyRepeat,
	env.StopReview,
	env.Mode,
}

// readSettingsFile は設定ファイルを JSON として読む。エラーの文は Python の str(exc) と同じ。
func readSettingsFile(root, rel string) (any, error) {
	return fsio.ReadJSON(ospath.Join(root, rel))
}

func objectOf(v any) *pyjson.Object {
	o, _ := v.(*pyjson.Object)
	return o
}

func listOf(v any) []any {
	l, _ := v.([]any)
	return l
}

// readableSettings は `.claude/settings.json` の中身。無い・読めない・形が違うときは nil（診断が壊れた設定で落ちない）。
func readableSettings(root string) *pyjson.Object {
	path := ospath.Join(root, projectSettingsRel)
	if !ospath.IsFile(path) {
		return nil
	}
	data, err := fsio.ReadJSON(path)
	if err != nil {
		return nil
	}
	return objectOf(data)
}

// stopReviewProblems は Stop の AI 判定の設定と、agent hook の登録の食い違い。値の書き誤り、登録の印と agent 型の
// Stop hook の片方だけ（計画が溜まるだけ・すべて未検査）、hook の重複、判定役の Bash を許す権限の欠け
// （agent 型 hook の中は dontAsk で動き、material を実行できない）を言う。
func stopReviewProblems(root string, conf *config.Settings) []rules.Problem {
	var out []rules.Problem
	warn := func(detail string) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereStopReview, Detail: detail})
	}
	valid := []config.Mode{config.Disable, config.DryRun, config.Enable}
	if v := declared(conf.StopReview); v != "" && !modeIn(v, valid) {
		warn(fmt.Sprintf("%s=%s。%s のどれでもないので%s として動いている", env.StopReview, v, joinModes(valid, "・"), config.DryRun))
	}
	hooks, fileFlag := stopReviewHooks(root)
	// ターミナルの --lint には settings.json の env が渡らないので、settings.json の env も読む。
	registered := conf.StopReviewAgent || fileFlag == "1"
	if registered && len(hooks) == 0 {
		warn(env.StopReviewAgent + "=1 だが、agent 型の Stop hook が .claude/settings.json に見つからない（計画が溜まるだけで判定されない）")
	}
	if len(hooks) > 0 && !registered {
		warn("agent 型の Stop hook があるが " + env.StopReviewAgent + "=1 が無い（判定計画が作られず、判定はすべて未検査になる）")
	}
	if len(hooks) > 1 {
		warn(fmt.Sprintf("agent 型の Stop hook が %d 件ある（同じ Stop で判定が重複する）", len(hooks)))
	}
	if len(hooks) > 0 && !allowsJudgeBash(root) {
		warn("判定役の Bash（stop-review material と record）を許す権限が .claude/settings.json の permissions.allow に無い" +
			"（判定役は dontAsk で動くので、材料を取れず、すべて未検査になる。register で足せる）")
	}
	return out
}

// allowsJudgeBash は permissions.allow に、判定役の `stop-review material` と `record` の両方があるか。
func allowsJudgeBash(root string) bool {
	allowed := map[string]bool{}
	for _, r := range listOf(objectOf(readableSettings(root).Value("permissions")).Value("allow")) {
		if s, ok := r.(string); ok {
			allowed[s] = true
		}
	}
	for _, rule := range stopreview.JudgeAllowRules() {
		if !allowed[rule] {
			return false
		}
	}
	return true
}

// stopReviewHooks は Stop にある ccnavi の agent 型 hook（prompt の先頭が目印のもの。ユーザ自身の agent 型 hook は
// 数えない）と、env の印の値。
func stopReviewHooks(root string) ([]string, string) {
	data := readableSettings(root)
	if data == nil {
		return nil, ""
	}
	var found []string
	for _, group := range listOf(objectOf(data.Value("hooks")).Value("Stop")) {
		for _, h := range listOf(objectOf(group).Value("hooks")) {
			hook := objectOf(h)
			if hook == nil || hook.Value("type") != "agent" {
				continue
			}
			if prompt, ok := hook.Value("prompt").(string); ok && strings.HasPrefix(prompt, stopreview.HookMark) {
				found = append(found, string([]rune(prompt)[:min(len([]rune(prompt)), stopReviewPromptShown)]))
			}
		}
	}
	return found, objectOf(data.Value("env")).String(env.StopReviewAgent)
}

// registered はそのイベントに ccnavi が登録されているか。設定ファイルが読めなければ known が偽（hook はユーザごとの
// 設定にも書けて、そちらはこの検証から見えないので「無い」と言い切らない）。コマンドに名前が含まれるかを、大文字小文字を
// 無視して見る（`${CCNAVI_ENTRY_PATH}` のように大文字で書かれる形が普通にある）。
func registered(root string, event hookio.Event) (yes, known bool) {
	data, err := readSettingsFile(root, projectSettingsRel)
	if err != nil {
		// 読めないことは projectSettings が言う。
		return false, false
	}
	for _, entry := range listOf(objectOf(objectOf(data).Value("hooks")).Value(string(event))) {
		for _, h := range listOf(objectOf(entry).Value("hooks")) {
			if command, ok := objectOf(h).Value("command").(string); ok && strings.Contains(pyfmt.Lower(command), "ccnavi") {
				return true, true
			}
		}
	}
	return false, true
}

// afterCheck は実行後チェックが実際に動く形かを見る。どちらも warn（実行前チェックは動いている）。宣言した保護領域が
// 引数に現れない書き込みに対して守られていない状態は、外から見ると守られている状態と区別が付かない。
func afterCheck(root string) []rules.Problem {
	yes, known := registered(root, hookio.PostToolUse)
	if !known {
		return nil
	}
	if !yes {
		// 走らないチェックに「見えない」とは言わない（直す先が 2 つあるように読めるだけになる）。
		return []rules.Problem{{Severity: rules.Warn, Rule: whereProject, Detail: projectSettingsRel +
			" の PostToolUse に ccnavi が登録されていない。実行後チェックは走らないので、ビルドの副作用やスクリプトが内部で開いた" +
			"ファイルによる保護領域の変更は誰も見ていない"}}
	}
	if _, unreadable := git.Read(git.TopLevel(root), git.StateTimeout); unreadable != "" {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereProject, Detail: fmt.Sprintf(
			"ワークツリーを読めない（%s）ので実行後チェックは何も検知しない。実行前チェックはこれまでどおり動く", unreadable)}}
	}
	return nil
}

// projectSettings は `.claude/settings.json` の env に、防御を無効化する値が無いかを見る。
// このファイルはエージェントが書き換えられるため、ここに書かれた off は、
// ユーザが起動のときに渡した off と見分けのつかない形で届いてしまう。
func projectSettings(root string) []rules.Problem {
	data, err := readSettingsFile(root, projectSettingsRel)
	if errors.Is(err, fs.ErrNotExist) {
		// 無いのは異常ではない。ccnavi は環境変数だけでも動く。
		return nil
	}
	if err != nil {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereProject, Detail: fmt.Sprintf("%s を読めない: %s", projectSettingsRel, fsio.ErrorText(err))}}
	}
	vars := objectOf(objectOf(data).Value("env"))
	if vars == nil {
		return nil
	}
	var out []rules.Problem
	if written, ok := vars.Value(env.Mode).(string); ok && written != "" {
		switch config.Mode(pyfmt.Lower(written)) {
		case config.Disable:
			out = append(out, rules.Problem{Severity: rules.Error, Rule: whereProject, Detail: fmt.Sprintf(
				"%s の env が %s=%s を宣言している。監視される側が書けるファイルから監視を止めている。"+
					"止めるならセッションを起動する側の環境から渡してください", projectSettingsRel, env.Mode, config.Disable)})
		case config.DryRun, config.Enable:
		default:
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProject, Detail: fmt.Sprintf(
				"%s の env の %s=%s はモードとして読めない。%s として扱う", projectSettingsRel, env.Mode, pyfmt.Repr(written), config.Enable)})
		}
	}
	return append(out, binPath(root, vars.Value(env.EntryPath))...)
}

// localSettings は `.claude/settings.local.json` の env に、承認に影響する値が無いかを見る。置ける例外は無い。
func localSettings(root string) []rules.Problem {
	data, err := readSettingsFile(root, localSettingsRel)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereProject, Detail: fmt.Sprintf("%s を読めない: %s", localSettingsRel, fsio.ErrorText(err))}}
	}
	vars := objectOf(objectOf(data).Value("env"))
	if vars == nil {
		return nil
	}
	var out []rules.Problem
	for _, name := range localForbidden {
		if _, ok := vars.Get(name); !ok {
			continue
		}
		out = append(out, rules.Problem{Severity: rules.Error, Rule: whereProject, Detail: fmt.Sprintf(
			"%s の env に %s がある。承認と判定に使われる値は手元だけのファイルに置かない（Chrome とターミナルの sh には"+
				"使われないので、同じ親子のチケットにプロセスごとに別の保護が掛かることになる）。"+
				"%s に置いてコミットするか、セッションを起動する側の環境から渡してください。", localSettingsRel, name, projectSettingsRel)})
	}
	return out
}

// binPath は env の実行ファイルのパスが、在るのに実行できないかを見る（error。hook は sh を直に起動するので 126 で
// 起動せず、判定が動いていない）。無いときは言わない（組み立ての前や、ユーザごとの設定で別のパスを渡す形がある）。
// Windows は実行ビットを持たないので見ない。
func binPath(root string, written any) []rules.Problem {
	path, ok := written.(string)
	if !ok || path == "" || ospath.Windows {
		return nil
	}
	full := path
	if !ospath.IsAbs(path) {
		full = ospath.Join(root, path)
	}
	if !ospath.IsFile(full) || executable(full) {
		return nil
	}
	return []rules.Problem{{Severity: rules.Error, Rule: whereProject, Detail: fmt.Sprintf(
		"%s の env の %s=%s は在るが実行できない。hook が起動しないので何も判定していない。実行ビットを付ける"+
			"（chmod +x %s）か、scripts/ccnavi-setup.sh を再実行してください", projectSettingsRel, env.EntryPath, path, path)}}
}

// executable は実行ビットのどれかが立っているか。os.access(X_OK) の近似で、持ち主以外にだけ立つ形は区別しない
// （infra に os.access の写しが無いため）。
func executable(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().Perm()&0o111 != 0
}
