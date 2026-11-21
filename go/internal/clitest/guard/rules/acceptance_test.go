package rules

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 受入テスト。標準入力に payload を 1 件渡し、読み返すのは標準出力・標準エラー・終了コード・記録だけ。
// 入口より内側の関数を呼ばないので、中身が動いてもテストは真であり続ける。

// guardOff は、コアファイルのバックアップと復元を切る環境。リポジトリ自身を使わない作業場所でも、
// 判定だけを見るテストは自己防衛の戻しに巻き込まれない。
var guardOff = map[string]string{env.GuardCoreFiles: "disable"}

// accept は見本のルールを据えた作業場所 ws に payload を 1 回渡す。モードは環境ではなくフラグで固定する。
func accept(t testing.TB, ws, mode string, body any) run.Result {
	t.Helper()
	return fire(t, ws, []string{"--mode", mode}, body, guardOff)
}

// shared は見本 rules.yml を据えた共有の作業場所（記録を読まないテストだけが使う）。
func shared(t testing.TB) string { return base.FixtureWorkspace(t, "rules.yml") }

func acceptBash(t testing.TB, command string) reply {
	t.Helper()
	return parse(t, accept(t, shared(t), "enable", preBash(command)))
}

func TestADenialCarriesAReasonAndAnAlternative(t *testing.T) {
	t.Parallel()
	r := accept(t, shared(t), "enable", preBash("git push origin main"))
	if r.Code != 0 {
		t.Errorf("判定は JSON で返すので終了コードは 0: %d", r.Code)
	}
	out := parse(t, r)
	if out.Decision != "deny" || out.Event != "PreToolUse" {
		t.Errorf("deny の応答でない: %+v", out)
	}
	// 代わりの手段を名指ししない拒否は、エージェントに手段を発明させる。
	mustHave(t, out.Reason, "ask the user")
}

func TestAPassedCallSaysNothing(t *testing.T) {
	t.Parallel()
	r := accept(t, shared(t), "enable", preBash("ls -la"))
	if r.Code != 0 || r.Stdout != "" {
		t.Errorf("通る呼び出しでは標準出力は空: code=%d %q", r.Code, r.Stdout)
	}
}

func TestEveryHitReasonComesBackInOneResponse(t *testing.T) {
	t.Parallel()
	// 1 つずつ返すとエージェントが往復することになる。
	mustHave(t, acceptBash(t, "sed -i s/a/b/ .env").Reason, "sed", "perl")
}

func TestAnUnknownEventDoesNotStopTheWork(t *testing.T) {
	t.Parallel()
	r := accept(t, shared(t), "enable", payload{
		"hook_event_name": "SomethingElse", "tool_name": "Bash", "tool_input": payload{"command": "git push"},
	})
	if r.Code != 0 || r.Stdout != "" {
		t.Errorf("判定を持たないイベントで止めてはいけない: code=%d %q", r.Code, r.Stdout)
	}
}

func TestADirectInvocationFailsInsteadOfSucceeding(t *testing.T) {
	t.Parallel()
	// payload 無しで 0 だと、設置を誤った hook が正常に見える。
	r := accept(t, shared(t), "enable", "")
	if r.Code == 0 {
		t.Error("payload 無しで成功した")
	}
	mustHave(t, r.Stderr, "hook")
}

func TestDryRunReportsWithoutActing(t *testing.T) {
	t.Parallel()
	out := parse(t, accept(t, shared(t), "dry-run", preBash("rm -rf /tmp/some-dir")))
	if out.Decision != "" {
		t.Errorf("dry-run は報告するだけで手を出さない: %q", out.Decision)
	}
	mustHave(t, out.Context, "rm -rf")
}

func TestDryRunStillStopsRawGit(t *testing.T) {
	t.Parallel()
	// 生の git は補助の保護で、dry-run でも止める（チケット制御が有効なとき）。
	if out := parse(t, accept(t, shared(t), "dry-run", preBash("git push origin main"))); out.Decision != "deny" {
		t.Errorf("deny のはず: %+v", out)
	}
}

func TestDisableJudgesNothing(t *testing.T) {
	t.Parallel()
	// disable は起動したユーザの環境からしか反映されない。フラグからは反映されない。
	r := fire(t, shared(t), nil, preBash("git push origin main"), map[string]string{env.Mode: "disable"})
	if r.Code != 0 || r.Stdout != "" {
		t.Errorf("何も判定しないはず: code=%d %q", r.Code, r.Stdout)
	}
}

// 理由が、対象・理由コード・出所を持ち、単独で読んで成立すること。

// parts は 1 回の応答に入った理由を、件ごと（空行で分かれる）に切り分けたもの。受け取った側が 1 件だけを切り出して読めることが要求そのもの。
func parts(reason string) []string {
	var out []string
	for _, p := range strings.Split(reason, "\n\n") {
		if strings.TrimSpace(p) != "" {
			out = append(out, p)
		}
	}
	return out
}

func TestAReasonNamesTheSubjectTheCodeAndTheSource(t *testing.T) {
	t.Parallel()
	got := parts(acceptBash(t, "git push origin main").Reason)
	if len(got) != 1 {
		t.Fatalf("1 件のはず: %q", got)
	}
	mustHave(t, got[0], "git push origin main", "DENY_COMMAND_PATTERN", "rule: .ccnavi/config/common/rules.yml:git-push")
	// ルールファイルのパスは要らない（id で辿れる）。
	mustNotHave(t, got[0], base.CommonPath(shared(t), "rules"))
}

func TestAFileReasonNamesTheResolvedTarget(t *testing.T) {
	t.Parallel()
	// 適用したのは来たままのパスではなく解いた先なので、対象もそちらを言う。
	out := parse(t, accept(t, shared(t), "enable", pre("Read", map[string]any{"file_path": "docs/../.env"})))
	got := parts(out.Reason)
	if len(got) != 1 {
		t.Fatalf("1 件のはず: %q", got)
	}
	mustHave(t, got[0], filepath.Join(base.Root(), ".env"), "DENY_PATH", "rule: .ccnavi/config/common/rules.yml:dotenv")
}

func TestEachOfSeveralReasonsReadsAloneAndPointsAtNoOther(t *testing.T) {
	t.Parallel()
	// 同じ出来事に複数の判定が同時にヒットするとき、どれがユーザの目に入るかは決まらない。
	out := acceptBash(t, "sed -i s/a/b/ .env")
	got := parts(out.Reason)
	if len(got) != 2 {
		t.Fatalf("2 つのルールにヒットしたはず: %q", got)
	}
	for _, part := range got {
		mustHave(t, part, "sed -i s/a/b/ .env", "[ccnavi] DENY_", "(rule: ")
	}
	mustNotHave(t, out.Reason, "the rules below", "the rules above", "listed below")
}

func TestEachUnreadReasonSaysItWasUnread(t *testing.T) {
	t.Parallel()
	// 読めなかったという断りは、先頭に 1 回だけ置くと、その下の 1 件だけを読んだユーザには届かない。
	got := parts(acceptBash(t, `bash -c "git push and read .env"`).Reason)
	if len(got) != 2 {
		t.Fatalf("2 件のはず: %q", got)
	}
	for _, part := range got {
		mustHave(t, part, "PARSE_UNCERTAIN", "生の文字列")
	}
}

// 何がコマンドかを狭める変更が安全なのは、今まで捕まえていたものが 1 つも抜けないときだけ。

func TestWritingAboutACommandIsNotRunningIt(t *testing.T) {
	t.Parallel()
	// ルールはコマンドについて書かれたものであって文字列についてではない。どれも実際に出た拒否。
	for _, command := range []string{
		`grep -n "git push" README.md`,
		`echo "git push origin main"`,
		`echo "git push を拒否する理由を書く"`,
		"# git push origin main",
		"echo $((1 << 2))",
		`find . \( -name x -o -name y \) -print`, // 括弧でグループ化した述語は読める形
		"grep -e '(' -n main.sh",
		"uv run python scratch.py", // ヒアドキュメントの代わりに勧める書き方は止めない
		"echo hi > f.txt",
		"psql -f query.sql",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if r := accept(t, shared(t), "enable", preBash(command)); r.Stdout != "" {
				t.Errorf("止めた: %s", r.Stdout)
			}
		})
	}
}

func TestAHeredocBodyIsNotReadAsAForbiddenWord(t *testing.T) {
	t.Parallel()
	// ヒアドキュメントそのものは別のルールで止まるので、返る理由はその 1 件だけになる。
	reason := acceptBash(t, "cat <<'EOF' > notes.md\ngit push origin main\nEOF").Reason
	mustHave(t, reason, "Heredocs are not used here")
	mustNotHave(t, reason, "git push is not run by the agent")
}

func TestEveryWayToTheSamePushIsStillStopped(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"git push origin main",
		"git   push",
		"cd /repo && git push",
		"echo hi; git push origin main",
		"/usr/bin/git push",
		"git \\\n  push origin main",
		"GIT_DIR=/repo/.git git push",
		"echo $(git push origin main)",
		`"git" push`,
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if got := acceptBash(t, command).Decision; got != "deny" {
				t.Errorf("通した: %q", got)
			}
		})
	}
}

func TestAnUnreadDenialSaysItWasUnread(t *testing.T) {
	t.Parallel()
	// 文字列をコードとして実行する呼び出しは読めないので生の文字列に適用する。それを言うことが「実行した」と「その語がある」の違い。
	unread := acceptBash(t, `bash -c "git push origin main"`)
	if unread.Decision != "deny" {
		t.Errorf("読めない呼び出しを通した: %+v", unread)
	}
	mustHave(t, unread.Reason, "生の文字列")
	mustNotHave(t, acceptBash(t, "git push origin main").Reason, "生の文字列")

	out := acceptBash(t, `find . -exec wc {} \; -delete`)
	mustHave(t, out.Reason, "生の文字列")
}

func TestHeredocsAreStoppedAndTheAlternativeIsNamed(t *testing.T) {
	t.Parallel()
	// ヒアドキュメントで書いたファイルは、Write / Edit の権限の宣言も編集後の検査も通らない。書き出す形もプログラムへ渡す形も止める。
	for _, command := range []string{
		"cat <<'EOF' > notes.md\nhello\nEOF",
		"cat <<EOF >> config.yml\na: 1\nEOF",
		"cat <<EOF | tee out.txt\nx\nEOF",
		"\tcat <<-EOF > f\n\tx\n\tEOF",
		"uv run python - <<'PY'\nprint(1)\nPY",
		`grep x <<< "$text"`,
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			out := acceptBash(t, command)
			if out.Decision != "deny" {
				t.Fatalf("通した: %+v", out)
			}
			// 止めるだけでは足りない。Write を名指ししないと、同じことを別の表記で書き直すだけになる。
			mustHave(t, out.Reason, "Write")
		})
	}
}

func TestAQuotedChevronPairStaysStoppedAsAToleratedFalsePositive(t *testing.T) {
	t.Parallel()
	// 走査は引用の中の << をヒアドキュメントと読まないが、トークンには出る。閉じない本文として縮退し、止まる。
	// 許容すると決めた誤検知なので、決めた側を固定する。返る文面は読み手に次の一手を残す（生の文字列）。
	out := acceptBash(t, `grep -n "<<" README.md`)
	if out.Decision != "deny" {
		t.Errorf("deny のはず: %+v", out)
	}
	mustHave(t, out.Reason, "生の文字列")
}

func TestAPathToAProtectedPlaceIsJudgedByThePlace(t *testing.T) {
	t.Parallel()
	// 守る対象は名前ではなく場所。同じ場所を指す別のパスでルールを外せてはいけない。
	for _, path := range []string{".env", "./.env", "docs/../.env", filepath.Join(base.Root(), ".env")} {
		t.Run(path, func(t *testing.T) {
			t.Parallel()
			out := parse(t, accept(t, shared(t), "enable", pre("Read", map[string]any{"file_path": path})))
			if out.Decision != "deny" {
				t.Errorf("すり抜けた: %+v", out)
			}
		})
	}
	// 空のパスに作業ディレクトリを返すと、対象の無い呼び出しがディレクトリへの操作に見える。
	if r := accept(t, shared(t), "enable", pre("Read", map[string]any{"file_path": ""})); r.Stdout != "" {
		t.Errorf("対象の無い呼び出しは判定しない: %s", r.Stdout)
	}
}

// 記録

// logged は payloads を順に通し、その間に増えた記録の行を返す。
func logged(t testing.TB, mode string, payloads ...any) []map[string]any {
	t.Helper()
	ws := withRules(t, "rules.yml")
	for _, p := range payloads {
		accept(t, ws, mode, p)
	}
	return records(t, ws)
}

func TestEveryCallIsRecordedIncludingPassedOnes(t *testing.T) {
	t.Parallel()
	got := logged(t, "enable",
		preBash("git push origin main"),
		preBash("go build ./..."),
		pre("WebSearch", map[string]any{"query": "something"}),
		payload{"hook_event_name": "SessionStart"},
		payload{"hook_event_name": "PreCompact"},
	)
	// 記録の無い呼び出しは、動かなかったガードと区別が付かない。
	want := []struct{ decision, reason string }{
		{"deny", ""},
		{"allow", ""},
		{"skip", "no-subject"},
		{"allow", ""}, // セッション開始は判定を持つイベント（大きい対象のバックアップを 1 度だけ取る）
		{"skip", "event-not-checked"},
	}
	if len(got) != len(want) {
		t.Fatalf("記録は %d 行のはず: %v", len(want), got)
	}
	for i, w := range want {
		if str(got[i], "decision") != w.decision || str(got[i], "reason") != w.reason {
			t.Errorf("%d 行目: %v（decision=%s reason=%q のはず）", i+1, got[i], w.decision, w.reason)
		}
	}
}

func TestDryRunRecordsTheVerdictItDidNotApply(t *testing.T) {
	t.Parallel()
	// warn で走らせる理由そのものが「何を止めるはずだったか」を数えること。
	got := logged(t, "dry-run", preBash("rm -rf /tmp/some-dir"))
	if len(got) != 1 {
		t.Fatalf("1 行のはず: %v", got)
	}
	if str(got[0], "decision") != "deny" || got[0]["enforced"] != false {
		t.Errorf("deny で enforced=false のはず: %v", got[0])
	}
	if r := strs(got[0], "rules"); len(r) != 1 || r[0] != "builtin:recursive-delete" {
		t.Errorf("rules = %v", r)
	}
}

func TestReadAndUnreadVerdictsAreRecordedApart(t *testing.T) {
	t.Parallel()
	got := logged(t, "enable", preBash("git push origin main"), preBash(`bash -c "git push origin main"`))
	if len(got) != 2 {
		t.Fatalf("2 行のはず: %v", got)
	}
	if _, has := got[0]["degraded"]; has {
		t.Errorf("読めたコマンドに degraded が付いている: %v", got[0])
	}
	// これが無いと、ガードが止めたもののうちどれだけが読み切れないまま出た判定なのかを記録が答えられない。
	if _, has := got[1]["degraded"]; !has {
		t.Errorf("生の文字列で下した判定に degraded が無い: %v", got[1])
	}
}

func TestAHitInsideAQuoteIsRecordedAsQuoted(t *testing.T) {
	t.Parallel()
	// 書いた側が文字のつもりでいた場所にヒットした判定を、あとから数えられるように。
	got := logged(t, "enable",
		preBash(`gh issue create --body "use $(git push) here"`),
		preBash("echo $(git push origin main)"),
	)
	if len(got) != 2 {
		t.Fatalf("2 行のはず: %v", got)
	}
	if q := strs(got[0], "quoted"); len(q) != 1 || q[0] != ".ccnavi/config/common/rules.yml:git-push" {
		t.Errorf("quoted = %v", q)
	}
	if _, has := got[1]["quoted"]; has || str(got[1], "decision") != "deny" {
		t.Errorf("引用の外の置換に quoted が付いている: %v", got[1])
	}
}
