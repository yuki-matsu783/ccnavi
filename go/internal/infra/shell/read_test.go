package shell

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"
)

// m は期待の文字列の ␀ を Sep に、␁ を WordSep にする。
func m(s string) string {
	return strings.NewReplacer("␀", Sep, "␁", WordSep).Replace(s)
}

// show は目印を見える形にする。失敗の文面で、どちらの目印が返ったかを言えるように。
func show(s string) string {
	return strings.NewReplacer(Sep, "␀", WordSep, "␁").Replace(s)
}

func readable(t *testing.T, src string) Reading {
	t.Helper()
	r := Read(src)
	if r.Degraded {
		t.Fatalf("Read(%q) が %s で諦めた。読めるはず", src, r.Reason)
	}
	return r
}

func TestReadText(t *testing.T) {
	cases := []struct{ src, text, bare string }{
		{"git push origin main", "git push origin main", ""},
		{"git   push", "git push", ""},
		{"/usr/bin/git push", "/usr/bin/git push", ""},
		{"cd /repo && git push", "cd /repo␀git push", ""},
		{"echo hi; git push", "echo hi␀git push", ""},
		// 2 行に割ったコマンドは 1 行で書いたのと同じ。
		{"git \\\n  push origin main", "git push origin main", ""},
		{`cat "/home/u/.env"`, "cat /home/u/.env", ""},
		// リダイレクトは空白の有無によらず演算子として残る。
		{"echo x>f", "echo x > f", ""},
		{"cat a>>b", "cat a >> b", ""},
		{"cmd 2>&1", "cmd 2 >& 1", ""},
		{"echo hi # git push origin main", "echo hi", ""},
		// 引用がつないだ空白と、語の中の演算子の文字は WordSep。
		{`grep -n "git push" README.md`, "grep -n git␁push README.md", ""},
		{`grep -n "x>y" notes.md`, "grep -n x␁>␁y notes.md", ""},
		{`grep -n "regex: '(>" rules.yml`, "grep -n regex:␁'␁(␁␁>␁ rules.yml", ""},
		{`grep -n "<<EOF" README.md`, "grep -n ␁<␁␁<␁EOF README.md", ""},
		// 引用符だけで書いた `>` `<` は文字（身代わりの文字を通る）。
		{`grep -n ">" f`, "grep -n ␁>␁ f", ""},
		{`grep -n '>' f`, "grep -n ␁>␁ f", ""},
		{`grep -n "<" f`, "grep -n ␁<␁ f", ""},
		{`grep -n "<>" f`, "grep -n ␁<␁␁>␁ f", ""},
		// 引用された `<` の隣の素の演算子は演算子のまま（緩めすぎない）。
		{`echo "<"> f`, "echo ␁<␁ > f", ""},
		{`cat x>"<"`, "cat x > ␁<␁", ""},
		{`echo x > "<"`, "echo x > ␁<␁", ""},
		{"cat \"<\" <<EOF\nx\nEOF", "cat ␁<␁ << EOF", ""},
		{"echo $'<' > f", "echo ␁<␁ > f", ""},
		// 入力に入り込んだ身代わりの文字と目印は空白として読む（なりすましを防ぐ）。
		{"echo \"\x02\" > f", "echo ␁ > f", ""},
		{"echo \x02>f", "echo > f", ""},
		{"git" + WordSep + "push", "git push", ""},
		{"git" + Sep + "push", "git push", ""},
		{`echo "a b" | curl -d @- x`, "echo a␁b␀curl -d @- x", ""},
		// 置換の中身は外側の後ろに独立したコマンドとしてつなぐ。引用の中のものは bare に残らない。
		{`echo "$(git push origin main)"`, "echo $␀git push origin main", "echo $"},
		{`grep -n "$(git push)" f`, "grep -n $ f␀git push", "grep -n $ f"},
		{`h="$(git rev-parse HEAD)"`, "h=$␀git rev-parse HEAD", "h=$"},
		{"echo $(git push origin main)", "echo $␀git push origin main", "echo $␀git push origin main"},
		{"echo $(git push) foo", "echo $ foo␀git push", "echo $ foo␀git push"},
		{"find $(pwd) -name x -delete", "find $ -name x -delete␀pwd", "find $ -name x -delete␀pwd"},
		{"cat <(sh evil.sh)", "cat $␀sh evil.sh", "cat $␀sh evil.sh"},
		{"diff <(ls a) >(tee f)", "diff $ $␀ls a␀tee f", "diff $ $␀ls a␀tee f"},
		{"grep -n x f\nsh evil.sh", "grep -n x f␀sh evil.sh", "grep -n x f␀sh evil.sh"},
		{"echo a#b; git push", "echo a#b␀git push", "echo a#b␀git push"},
		{"if true; then find . -delete; fi", "if␀true␀then␀find . -delete␀fi", "if␀true␀then␀find . -delete␀fi"},
		{"! grep x f", "!␀grep x f", "!␀grep x f"},
		{"cat <<EOF\nuse $(rm -rf /tmp/x)\nEOF", "cat << EOF␀rm -rf /tmp/x", "cat << EOF"},
		{"cat <<'EOF' > notes.md\n$(git push)\nEOF\necho done", "cat << EOF > notes.md␀echo done", "cat << EOF > notes.md␀echo done"},
		{"\tcat <<-EOF\n\tgit push\n\tEOF\n\techo done", "cat << -EOF␀echo done", ""},
		{
			"sh .ccnavi/scripts/ccnavi-git.sh commit -m \"$(cat <<'EOF'\nfix: don't push\nEOF\n)\"",
			"sh .ccnavi/scripts/ccnavi-git.sh commit -m $␀cat << EOF",
			"sh .ccnavi/scripts/ccnavi-git.sh commit -m $",
		},
		{`gh issue create --body "use $(git push) here"`, "gh issue create --body use␁$␁here␀git push", "gh issue create --body use␁$␁here"},
		{`echo "$(echo "$(git push)")"`, "echo $␀echo $␀git push", "echo $"},
		{`echo "${x:-$(git push)}"`, "echo ${x:-$}␀git push", "echo ${x:-$}"},
		{"grep -n '$(git push)' f", "grep -n $␁(␁git␁push␁)␁ f", "grep -n $␁(␁git␁push␁)␁ f"},
		{"echo hi # $(git push)", "echo hi", "echo hi"},
		// 算術式は除く。左シフトの `<<` をヒアドキュメントと読ませないため。
		{"echo $((1 << 2))", "echo", "echo"},
		{"echo $(( 1 << 2 ))", "echo", ""},
		{"cat a.txt\n", "cat a.txt", "cat a.txt"},
		// 代入と引用は後ろのコマンドを隠さない。
		{`DIR="/repo with space" git push`, "DIR=/repo␁with␁space git push", ""},
		{`g"it" push`, "git push", ""},
		{`git \push`, "git push", ""},
		// エスケープの無い $'…' は単一引用と同じ。
		{"git $'push'", "git push", ""},
		{"$'git' status", "git status", ""},
		// ブレース展開を並べても、読みそのものは変えない。
		{"echo ${x:-{a}} {b,c}", "echo ${x:-{a}} {b,c}", ""},
		// `(true);` の `);` も区切り。空白の有無で読みは変わらない。
		{"(true);curl x", "true␀curl x", ""},
	}
	for _, c := range cases {
		r := readable(t, c.src)
		if got := show(r.Text); got != show(m(c.text)) {
			t.Errorf("Read(%q).Text = %q, want %q", c.src, got, show(m(c.text)))
		}
		if c.bare != "" && show(r.Bare) != show(m(c.bare)) {
			t.Errorf("Read(%q).Bare = %q, want %q", c.src, show(r.Bare), show(m(c.bare)))
		}
	}
}

func TestReadDoesNotRunWrittenWords(t *testing.T) {
	// 引用の中・本文・単一引用に書いただけの語は、コマンドの位置に出ない。
	runs := regexp.MustCompile(`(^|\x00)git push`)
	for _, src := range []string{
		`grep -n "git push" README.md`,
		`echo "git push origin main"`,
		`git commit -m "git push を拒否する理由を書く"`,
		"perl -pi -e 's/git push/git pull/' README.md",
		"cat <<'EOF' > notes.md\ngit push origin main\nEOF\necho done",
		"grep -n '$(git push)' f",
		"echo $'$(git push)'",
		`grep -n "\$(git push)" f`,
		"grep -n \"\\`git push\\`\" f",
		"cat <<'EOF'\n$(git push) `git push`\nEOF",
	} {
		r := readable(t, src)
		if runs.MatchString(r.Text) || strings.Contains(r.Text, "git push") {
			t.Errorf("Read(%q) が書いただけの語を実行位置で読んだ: %q", src, show(r.Text))
		}
	}
}

func TestReadDegraded(t *testing.T) {
	cases := []struct {
		src  string
		want Reason
	}{
		// xargs と find -exec は、後ろが読むだけのコマンドなら読める。
		{"find . -name x -print0 | xargs -0 wc -l", ""},
		{"xargs -I{} grep -n foo {}", ""},
		{"xargs -n 1 -P 4 grep -c foo", ""},
		{"xargs -n 2 grep x", ""}, // 値の 2 を実行される語と読まない
		{"xargs --max-args=3 sort", ""},
		{"/usr/bin/xargs /usr/bin/GREP.exe x", ""},
		{"find . -exec wc {} +", ""},
		{"find . -name x -exec grep -n foo {} \\; -print", ""},
		{`find . -type f \( -name "*.sh" -o -name "*.md" \) -not -path "./.git/*"`, ""},
		{"grep -rn ';' -n main.sh", ""},
		// 後ろが一覧に無いか決まらなければ、コードとして実行する形。
		{"xargs rm", ReasonTakenAsCode},
		{"xargs -I{} sh -c 'echo {}'", ReasonTakenAsCode},
		{"xargs cat", ReasonTakenAsCode},
		{"xargs", ReasonTakenAsCode},
		{"xargs -I{} {}", ReasonTakenAsCode},
		{"xargs --arg-file grep rm", ReasonTakenAsCode},
		{"ls | xargs grep x | xargs rm", ReasonTakenAsCode},
		{"find . -exec rm {} \\;", ReasonTakenAsCode},
		// find の続き（`\;` の後ろ）にある書き込み・実行の述語も見逃さない。
		{"find . -exec wc {} \\; -delete", ReasonTakenAsCode},
		{"find . -exec wc {} \\; -exec rm {} \\;", ReasonTakenAsCode},
		{"find . \\( -name x -exec rm {} \\; \\) -print", ReasonTakenAsCode},
		{"find . -name x -exec grep foo {} \\; -fprint /tmp/out", ReasonTakenAsCode},
		{`bash -c "git push"`, ReasonTakenAsCode},
		{`bash -lc "git push"`, ReasonTakenAsCode},
		{`sudo -u deploy sh -c "git push"`, ReasonTakenAsCode},
		{`eval "$command"`, ReasonTakenAsCode},
		{"eval grep x f", ReasonTakenAsCode},
		{`echo "$(xargs rm < f)"`, ReasonTakenAsCode},
		// 閉じないもの。
		{`echo "git push`, ReasonUnterminated},
		{"echo 'git push", ReasonUnterminated},
		{"cat <<EOF\ngit push\n", ReasonUnterminated},
		{"cat <<'EOF' > notes.md", ReasonUnterminated},
		{`echo "$(git push"`, ReasonUnterminated},
		{"echo $(git push", ReasonUnterminatedSubst},
		// 引用だけの `<<` は演算子と区別できない（許容した誤検知）。
		{`grep -n "<<" f`, ReasonUnterminated},
		{"echo `git push", ReasonBackquote},
		{"echo \"`git push\"", ReasonBackquote},
		{`echo "$(case a in a) git push;; esac)"`, reasonAmbiguousSubst},
		{"echo $'a\\nb'", ReasonAnsiC},
		{"git $'\\x70ush'", ReasonAnsiC},
		{"printf $'\\''", ReasonAnsiC},
		{`echo "$'x'"`, ""},
		// 書き込みのリダイレクト先が変数か置換なら、書く先が読めない。読み込み・fd の複製・引数の変数は読める。
		{`echo x > "$(echo .claude/settings.json)"`, ReasonExpandedTarget},
		{`echo x > $X`, ReasonExpandedTarget},
		{`echo x >> "${HOME}/a"`, ReasonExpandedTarget},
		{`echo x 2> "$LOG"`, ReasonExpandedTarget},
		{`echo x &> $(pwd)/out`, ReasonExpandedTarget},
		{`ls; echo "$(echo x > $X)"`, ReasonExpandedTarget},
		{`echo x > out.txt 2>&1`, ""},
		{`cat < $X`, ""},
		{`echo $X > out.txt`, ""},
	}
	for _, c := range cases {
		r := Read(c.src)
		if r.Reason != c.want || r.Degraded != (c.want != "") {
			t.Errorf("Read(%q) = (%v, %q), want %q", c.src, r.Degraded, r.Reason, c.want)
		}
	}
}

func nested(depth int) string {
	return "echo " + strings.Repeat(`"$(echo `, depth) + "git push" + strings.Repeat(`)"`, depth)
}

func TestNestingLimit(t *testing.T) {
	if r := Read(nested(maxDepth)); r.Degraded {
		t.Fatalf("16 階層は読める: %s", r.Reason)
	}
	for _, depth := range []int{maxDepth + 1, 400} {
		r := Read(nested(depth))
		if !r.Degraded || r.Reason != reasonAmbiguousSubst {
			t.Errorf("depth %d: (%v, %q)", depth, r.Degraded, r.Reason)
		}
	}
}

// TestScanFrameLimit は Python の再帰の上限に当たっていた深さで、走査ごと止めること。止めると外側の
// コマンドも層を作らない（Python で測った境目）。
func TestScanFrameLimit(t *testing.T) {
	cases := []struct {
		open, close string
		ok, stops   int
	}{
		{`"$(echo `, `)"`, 198, 199},
		{"$(echo ", ")", 331, 332},
		{"${x:-$(echo ", ")}", 198, 199},
	}
	for _, c := range cases {
		build := func(n int) string {
			return "env rm a " + strings.Repeat(c.open, n) + "x" + strings.Repeat(c.close, n)
		}
		if Read(build(c.ok)).Unwrapped == "" {
			t.Errorf("%q x %d は層を作るはず", c.open, c.ok)
		}
		if got := Read(build(c.stops)); got.Unwrapped != "" || got.Reason != reasonAmbiguousSubst {
			t.Errorf("%q x %d は走査で止まるはず: %q %q", c.open, c.stops, got.Reason, show(got.Unwrapped))
		}
	}
}

// substMark は切り出した中身がコマンドの先頭に来たかどうか。
var substMark = regexp.MustCompile(`(^|\x00)zzmark($|[ \x00])`)

// TestShellRunsOnlyWhatShellRuns の期待は、M を「呼ばれたら記録を残す関数」にして bash 3.2 と zsh で
// 走らせた結果。中=引用の中から切り出す、外=引用の外から切り出す、-=切り出さない、
// 割れる=シェルで答えが分かれる（縮退か切り出すかのどちらか）、それ以外は縮退の理由。
func TestShellRunsOnlyWhatShellRuns(t *testing.T) {
	cases := []struct{ id, src, want string }{
		{"01", `echo "$(M)"`, "中"},
		{"02", "echo \"`M`\"", "backquote"},
		{"03", `echo "\$(M)"`, "-"},
		{"04", "echo \"\\`M\\`\"", "-"},
		{"05", `echo "\\$(M)"`, "中"},
		{"06", "echo '$(M)'", "-"},
		{"07", "echo '`M`'", "-"},
		{"08", "echo $'$(M)'", "-"},
		{"09", `echo "it's $(M)"`, "中"},
		{"10", "echo hi # $(M)", "-"},
		{"11", "echo a#$(M)", "外"},
		{"12", "cat <<'EOF'\n$(M) `M`\nEOF", "-"},
		{"13", "cat <<\"EOF\"\n$(M) `M`\nEOF", "-"},
		{"14", "cat <<\\EOF\n$(M) `M`\nEOF", "-"},
		{"15", "cat <<EOF\n$(M)\nEOF", "中"},
		{"16", "cat <<EOF\nuse `M` here\nEOF", "backquote"},
		{"17", "echo \"$(cat <<'EOF'\nfix: use `M` and $(M) (see 6)\ndon't\nEOF\n)\"", "-"},
		{"18", "echo \"$(cat <<EOF\nuse `M`\nEOF\n)\"", "backquote"},
		{"19", `echo "$(echo "$(M)")"`, "中"},
		{"20", "echo `echo \\`M\\``", "backquote"},
		{"21", `echo "${x:-$(M)}"`, "中"},
		{"22", "echo \"${x:-`M`}\"", "backquote"},
		{"23", `echo "$((1 + 2))"`, "-"},
		{"24", `echo "$(( $(M) + 1 ))"`, "中"},
		{"25", `echo "$( (M) )"`, "中"},
		{"26", "cat <(M)", "外"},
		{"27", `echo "<(M)"`, "-"},
		{"28", `echo "$(case a in a) M;; esac)"`, "割れる"},
		{"29", `echo "$(echo "a)b"; M)"`, "中"},
		{"30", `echo "$(echo 'a)b'; M)"`, "中"},
		{"31", `echo "$(echo \")\"; M)"`, "-"},
		{"32", "echo \"$(echo a # )\nM)\"", "中"},
		{"33", `echo "$(echo '$(M)')"`, "-"},
		{"34", `echo "$(M"`, "unterminated-quote"},
		{"35", "echo \"`M\"", "backquote"},
		{"36", `cat <<< "$(M)"`, "中"},
		{"37", "echo \"$(\nM\n)\"", "中"},
		{"38", `h="$(M)"`, "中"},
		{"39", "h=$(M)", "外"},
		{"40", "h=`M`", "backquote"},
		{"41", `echo "\"$(M)\""`, "中"},
		{"42", `a=(1); echo "${a[$(M)]}"`, "中"},
		{"43", `echo "$[1+2]"`, "-"},
		{"44", `echo x > "$(M)"`, "expanded-redirect-target"}, // 書き込み先が置換なので読めない形
		{"45", `for f in "$(M)"; do :; done`, "中"},
		{"46", `[[ "$(M)" = x ]]`, "中"},
		{"47", "echo hi\nM", "外"},
		{"48", "ls |\nM", "外"},
		{"49", "echo hi # x\nM", "外"},
		{"50", "cat <<EOF\nx\nEOF\nM", "外"},
		{"51", "cat <<'EOF'\nx $(y)\nEOF\nM", "外"},
		{"52", "if true\nthen M\nfi", "外"},
		{"53", "diff <(echo a) >(M)", "外"},
		{"54", "cat < <(M)", "外"},
		{"55", "echo a#b; M", "外"},
		{"56", "echo a#b\nM", "外"},
		{"57", "echo $#; M", "外"},
		{"58", "echo ${#x}; M", "外"},
		{"59", "echo \"$(cat <<'EOF'\nfix: don't\nEOF\n)\"; M", "割れる"},
		{"60", "case a in a) M;; esac", "外"},
		{"61", "cat <<EOF\nx\n EOF\nEOF\nM", "外"},
		{"62", "cat <<A <<B\na\nA\nb $(M)\nB", "中"},
		{"63", "echo \"a\nM\"", "-"},
		{"64", "echo 'a #b'; M", "外"},
		{"65", "echo hi # don't\nM", "外"},
	}
	for _, c := range cases {
		r := Read(strings.ReplaceAll(c.src, "M", "zzmark"))
		var got string
		switch {
		case r.Degraded:
			got = string(r.Reason)
		case substMark.MatchString(r.Text) && substMark.MatchString(r.Bare):
			got = "外"
		case substMark.MatchString(r.Text):
			got = "中"
		default:
			got = "-"
		}
		if c.want == "割れる" && got == "-" || c.want != "割れる" && got != c.want {
			t.Errorf("%s %q: got %s, want %s (%q)", c.id, c.src, got, c.want, show(r.Text))
		}
	}
}

// layers は Unwrapped を層ごとに割り、語の中の目印を空白に戻す。
func layers(src string) []string {
	r := Read(src)
	if r.Unwrapped == "" {
		return nil
	}
	return strings.Split(strings.ReplaceAll(r.Unwrapped, WordSep, " "), Sep)
}

func TestUnwrapped(t *testing.T) {
	cases := []struct {
		src  string
		want []string
	}{
		// 代入（足し込みと配列の要素も）。
		{"FOO=1 rm x", []string{"rm x"}},
		{"a+=1 rm x", []string{"rm x"}},
		{"a[1]=x rm x", []string{"rm x"}},
		{"git.exe status", []string{"git status"}},
		// 実行役のコマンドと、飛ばすオプション・値・位置引数。
		{"env FOO=1 BAR=2 rm x", []string{"rm x"}},
		{"env a.b=1 rm x", []string{"rm x"}}, // env と sudo は `=` を含めば代入
		{"sudo a+=1 rm x", []string{"rm x"}},
		{"env -u HOME rm x", []string{"rm x"}},
		{"env --unset HOME rm x", []string{"rm x"}},
		{"env -- rm x", []string{"rm x"}},
		{"command rm x", []string{"rm x"}},
		{"exec -a name rm x", []string{"rm x"}},
		{"time -f %e rm x", []string{"rm x"}},
		{"nice -n 5 rm x", []string{"rm x"}},
		{"sudo -E -u me rm x", []string{"rm x"}},
		{"doas -u me rm x", []string{"rm x"}},
		{"timeout -k 1 5 rm x", []string{"rm x"}},
		{"stdbuf -o0 rm x", []string{"rm x"}},
		{"stdbuf -o 0 rm x", []string{"rm x"}},
		{"chrt 10 rm x", []string{"rm x"}},
		{"ionice -c 3 rm x", []string{"rm x"}},
		{"taskset 1 rm x", []string{"rm x"}},
		// シェルにファイルを渡す形はスクリプトで止まる。
		{"sh -x x.sh a", []string{"x.sh a"}},
		{". x.sh a", []string{"x.sh a"}},
		{"source x.sh a", []string{"x.sh a"}},
		// 文字列を読み直す形。
		{"sh -c 'rm x'", []string{"rm x"}},
		{"bash -lc 'rm x'", []string{"rm x"}},
		{"eval 'rm x'", []string{"rm x"}},
		{"xargs -n 1 -I {} rm {}", []string{"rm {}"}},
		{"find . -execdir rm {} +", []string{"rm {}"}},
		{"find . -okdir rm {} \\;", []string{"rm {}"}},
		{"find . -exec grep -l a {} \\; -exec rm {} \\;", []string{"grep -l a {}", "rm {}"}},
		// 途中の層も外側から並ぶ。
		{"env sh .ccnavi/scripts/ccnavi-user-agree.sh", []string{"sh .ccnavi/scripts/ccnavi-user-agree.sh", ".ccnavi/scripts/ccnavi-user-agree.sh"}},
		{"/usr/bin/env rm x", []string{"env rm x", "rm x"}},
		{"sudo -u me sh -c 'rm x'", []string{"sh -c rm x", "rm x"}},
		{">/dev/null env rm x", []string{"env rm x", "rm x"}},
		{"2>&1 env rm x", []string{"env rm x", "rm x"}},
		// 深さは 4 まで。
		{"env env env env rm x", []string{"env env env rm x", "env env rm x", "env rm x", "rm x"}},
		{"env env env env env rm x", []string{"env env env env rm x", "env env env rm x", "env env rm x", "env rm x"}},
		// コマンドごとの層がつながる。置換の中も。
		{"env rm a; nohup rm b", []string{"rm a", "rm b"}},
		{"echo $(env rm x)", []string{"rm x"}},
		// 実行役でないコマンドの引数に書いた名前は実行されない。
		{"echo env rm x", nil},
		{"grep -rn sudo docs", nil},
		// 閉じない引用とヒアドキュメントでは層を推測しない。
		{"env rm 'x", nil},
		{`sudo -u me sh -c "rm x`, nil},
		{"env cat <<EOF\nrm x\n", nil},
	}
	for _, c := range cases {
		if got := layers(c.src); !slices.Equal(got, c.want) {
			t.Errorf("layers(%q) = %q, want %q", c.src, got, c.want)
		}
	}
}

func TestUnwrappedWhenDegraded(t *testing.T) {
	// 読み切れない形こそ中を見たい。層を作り、縮退と理由は残す。
	cases := map[string][]string{
		"echo a | xargs rm x":             {"rm x"},
		"find . -name a -exec rm {} \\;":  {"rm {}"},
		`sudo -u deploy sh -c "git push"`: {"sh -c git push", "git push"},
	}
	for src, want := range cases {
		r := Read(src)
		if !r.Degraded || r.Reason != ReasonTakenAsCode || !slices.Equal(layers(src), want) {
			t.Errorf("Read(%q) = (%v, %q, %q)", src, r.Degraded, r.Reason, layers(src))
		}
	}
	r := Read(`sudo -u me sh -c "rm x"`)
	if want := []Layer{{"sudo", false}, {"sh", false}}; !slices.Equal(r.Layers, want) {
		t.Errorf("Layers = %v, want %v", r.Layers, want)
	}
}

func moved(t *testing.T, src string) []string {
	t.Helper()
	r := readable(t, src)
	if r.Moved == "" {
		return nil
	}
	return strings.Split(strings.ReplaceAll(r.Moved, WordSep, " "), Sep)
}

func TestMovedContains(t *testing.T) {
	rules := ".ccnavi/config/common/rules.yml"
	cases := []struct{ src, want string }{
		// 守られた場所へ入ってから書く形。
		{"cd .ccnavi/config/common && echo x > rules.yml", "echo .ccnavi/config/common/x > " + rules},
		{"cd .ccnavi && echo x > config/common/rules.yml", "echo .ccnavi/x > " + rules},
		{"(cd .ccnavi/config/common && echo x > rules.yml)", "echo .ccnavi/config/common/x > " + rules},
		{"cd .claude && rm -rf hooks", "rm -rf .claude/hooks"},
		{"cd .ccnavi && cd config/common && rm rules.yml", "rm " + rules},
		// `..` を畳む。起点より上に出る `..` は残す。末尾の区切りも残す。
		{"cd .claude/worktrees/w && echo x > ../../scripts/ccnavi-git.sh", "echo .claude/worktrees/w/x > .claude/scripts/ccnavi-git.sh"},
		{"cd a/b && rm ./x", "rm a/b/x"},
		{"cd a && rm ../../x", "rm ../x"},
		{"cd /tmp && rm x", "rm /tmp/x"},
		{"cd .claude && rm -rf hooks/", "rm -rf .claude/hooks/"},
		// 名前の前のリダイレクトとコマンドの無い形も、行き先はパス。
		{"cd .claude && > settings.json", "> .claude/settings.json"},
		{"cd .claude && >settings.json cat /tmp/x", "> .claude/settings.json cat /tmp/x"},
		// 値にパスを取る語は値だけ。
		{"cd .claude && dd if=/tmp/x of=settings.json", "dd if=/tmp/x of=.claude/settings.json"},
		// 置換の中で移った先も並ぶ。
		{`echo "$(cd .claude && echo x > settings.json)"`, "echo .claude/x > .claude/settings.json"},
		{"x=$(cd .claude && rm settings.json)", "rm .claude/settings.json"},
		{"builtin cd .claude && rm settings.json", "rm .claude/settings.json"},
		{"command cd .claude && rm settings.json", "rm .claude/settings.json"},
		// 実行役の中のコマンドにも継ぎ足す。名前には継ぎ足さない。層も並ぶ。
		{"cd .claude && sudo -u me rm settings.json", "sudo -u me rm .claude/settings.json"},
		{"cd .claude && sudo -u me rm settings.json", "rm .claude/settings.json"},
		{"cd .claude && timeout 5 rm settings.json", "timeout 5 rm .claude/settings.json"},
		{"cd .claude && exec > settings.json", "exec > .claude/settings.json"},
		// `env -C` と `sudo --chdir` はその 1 本だけ居場所を移す。
		{"env -C .claude rm settings.json", "env -C .claude rm .claude/settings.json"},
		{"sudo --chdir=.claude rm settings.json", "sudo --chdir=.claude rm .claude/settings.json"},
		{"cd docs && env -C ../.claude rm settings.json", "env -C ../.claude rm .claude/settings.json"},
		// ブレースと cd の連なりは漏れてよい。`$( )` の中の cd は外へ出ない。
		{"{ cd a; }; ls b", "ls a/b"},
		{"cd a && cd b; ls c", "ls a/b/c"},
		{"cd a && echo $(cd b && pwd); ls c", "ls a/c"},
	}
	for _, c := range cases {
		if got := moved(t, c.src); !slices.Contains(got, c.want) {
			t.Errorf("moved(%q) = %q, want it to contain %q", c.src, got, c.want)
		}
	}
}

func TestMovedExact(t *testing.T) {
	cases := []struct {
		src  string
		want []string
	}{
		// 移っていない・継ぎ足す先が無い形は並べない。
		{"echo x > .ccnavi/config/common/rules.yml", nil},
		{"cd .claude", nil},
		{"cd .ccnavi && cd common", nil},
		{"cd .ccnavi && rm -rf /tmp/x", nil},
		// fd の番号には継ぎ足さない。
		{"cd .ccnavi && rm ../a 2>&1", []string{"rm a 2 >& 1"}},
		{"env -C $X rm settings.json", nil},
		// サブシェルは出入りで戻す。`);` が 1 語でも同じ。
		{"cd a && (cd b && ls c) && ls d", []string{"ls a/b/c", "ls a/d"}},
		{"cd a && (cd b && ls c);ls d", []string{"ls a/b/c", "ls a/d"}},
		{"cd a && ( ( cd b ); cd c ); ls d", []string{"ls a/d"}},
		// `|` `&` の左はサブシェル。コマンド列の頭で居た場所までは戻る。
		{"cd .claude & echo x > settings.json", nil},
		{"cd .claude | echo x > settings.json", nil},
		{"cd .claude && cd docs | rm ../x", []string{"rm x"}},
		// case の枝は読まない。
		{"(case x in x) cd .claude;; esac); echo x > settings.json", nil},
		// 行き先を読めない形は継ぎ足さないだけで、縮退させない（縮退すると生の文字列で見て、
		// 今は止まっている形が通る）。
		{"cd - && rm x", nil},
		{"cd && rm x", nil},
		{"cd $HOME && rm x", nil},
		{`cd "$(git rev-parse --show-toplevel)" && rm x`, nil},
		{"cd ~/work && rm x", nil},
		{"cd .cc* && rm x", nil},
		{"cd --foo && rm x", nil},
		{"pushd .claude && rm settings.json", nil},
		{"cd a b && rm x", nil},
		{"cd .claude && rm settings.json && cd - && rm x", []string{"rm .claude/settings.json"}},
		{"cd - && cd /tmp && rm x", []string{"rm /tmp/x"}},
	}
	for _, c := range cases {
		if got := moved(t, c.src); !slices.Equal(got, c.want) {
			t.Errorf("moved(%q) = %q, want %q", c.src, got, c.want)
		}
	}
	// 書かれた表記は動かさない（コマンドの頭に固定したルールが外れる）。
	if got := Read("cd .claude && echo x > settings.json").Text; got != "cd .claude"+Sep+"echo x > settings.json" {
		t.Errorf("Text = %q", show(got))
	}
	for _, c := range moved(t, "cd .ccnavi && rm -rf common") {
		if !strings.HasPrefix(c, "rm ") {
			t.Errorf("名前に継ぎ足した: %q", c)
		}
	}
}

func TestMovedLimits(t *testing.T) {
	// `cd a` を並べると居場所が伸びる。上限が無いと二乗になる。
	chain := "cd a " + strings.Repeat("&& cd a ", 4000) + "&& rm x"
	started := time.Now()
	r := Read(chain)
	if spent := time.Since(started); spent > time.Second {
		t.Errorf("%v かかった", spent)
	}
	if r.Degraded || len(r.Moved) >= 100_000 {
		t.Errorf("degraded=%v len(moved)=%d", r.Degraded, len(r.Moved))
	}
	words := make([]string, movedWords)
	for i := range words {
		words[i] = fmt.Sprintf("rm x%d", i)
	}
	if got := moved(t, "cd a && "+strings.Join(words, " && ")); len(got) >= movedWords {
		t.Errorf("語数の上限を越えて継ぎ足した: %d", len(got))
	}
}

func TestPlacedCommands(t *testing.T) {
	got := PlacedCommands("cd /tmp && ( cd /x && ls ); echo > f", false)
	want := []Placed{{[]string{"ls"}, "/x", true}, {[]string{"echo", ">", "f"}, "/tmp", true}}
	if len(got) < 2 || !placedEqual(got[len(got)-2:], want) {
		t.Errorf("PlacedCommands = %v", got)
	}
	for _, src := range []string{"(true);curl x", "echo '(); x' ; ls", `echo "); y" ; ls`, "echo $((1+2)); ls"} {
		p := PlacedCommands(src, false)
		if last := p[len(p)-1].Words; !slices.Equal(last, []string{"ls"}) && !slices.Equal(last, []string{"curl", "x"}) {
			t.Errorf("PlacedCommands(%q) の最後 = %q", src, last)
		}
	}
	if p := PlacedCommands("(case x in x) cd a;; esac)", false); p[0].Known {
		t.Errorf("case の中の居場所は読めない: %v", p)
	}
	if PlacedCommands("echo 'x", false) != nil {
		t.Error("読み切れない形は nil")
	}
}

func placedEqual(a, b []Placed) bool {
	return slices.EqualFunc(a, b, func(x, y Placed) bool {
		return slices.Equal(x.Words, y.Words) && x.Here == y.Here && x.Known == y.Known
	})
}

func TestPlacedRenderedLayers(t *testing.T) {
	// 層を読み直しても、引用の中の `<` `>` は演算子にならない。
	operators := func(text string) []string {
		var out []string
		for _, p := range PlacedCommands(strings.ReplaceAll(text, Sep, "\n"), true) {
			for _, w := range p.Words {
				if w == "<" || w == ">" {
					out = append(out, w)
				}
			}
		}
		return out
	}
	for _, src := range []string{
		"sh x.sh log --format='%an <%ae>'",
		`sh x.sh commit -m "<a@b>"`,
		"echo '<' '>' 'a<' '>b' 'a>b'",
		`echo "$(echo '<x>')"`,
		`cd /w && sh x.sh commit -m "a <b@c.d>"`,
	} {
		r := Read(src)
		for _, text := range []string{r.Unwrapped, r.Moved} {
			if got := operators(text); got != nil {
				t.Errorf("%q の層に演算子が残った: %q", src, got)
			}
		}
	}
	// 打たれたコマンドの目印では演算子を隠せない。
	if !slices.Contains(PlacedCommands("echo x \x01>\x01 f", false)[0].Words, ">") {
		t.Error("入力の WordSep で演算子を隠せた")
	}
}

func TestBraces(t *testing.T) {
	cases := []struct {
		src  string
		want []string
	}{
		{"{git,push,origin,main}", []string{"{git,push,origin,main}"}},
		{"cp f{,.bak}", []string{"{,.bak}"}},
		{"echo {1..3}", []string{"{1..3}"}},
		{"echo {a..c}", []string{"{a..c}"}},
		{"echo {-2..1}", []string{"{-2..1}"}},
		// zsh（と bash 4 以降）だけが広げる形。
		{"echo {1..5..2}", []string{"{1..5..2}"}},
		{"echo {1..a}", []string{"{1..a}"}},
		{"echo ${x:-{a,b}}", []string{"{a,b}"}},
		// 中に引用や置換があっても、カンマが引用の外なら広がる。
		{`echo {a,"b c"}`, []string{`{a,"b c"}`}},
		{"echo {a,$(echo b)}", []string{"{a,$(echo b)}"}},
		{"echo $(echo p){a,b}", []string{"{a,b}"}},
		// 入れ子は外側だけ。外側が閉じなければ内側。
		{"echo {a,{b,c}}", []string{"{a,{b,c}}"}},
		{"echo {x{a,b}", []string{"{a,b}"}},
		{"echo a{b,c}d{e,f}", []string{"{b,c}", "{e,f}"}},
		{"echo {,}", []string{"{,}"}},
		// 生の CR は語を分けない（bash は広げる）。
		{"{git,\rpush,origin,main}", []string{"{git,\rpush,origin,main}"}},
		// 代入の右辺・case・[[ ]] はシェルが広げないが並べる（許容した誤検知）。
		{"x={a,b}", []string{"{a,b}"}},
		{"[[ $f == *.{jpg,png} ]]", []string{"{jpg,png}"}},
		// 置換の中身と読み直す文字列の中も。外側と読み直しの両方で見つけても 1 件。
		{`echo "$({git,push})"`, []string{"{git,push}"}},
		{"cat <<EOF\n$({git,push})\nEOF", []string{"{git,push}"}},
		{"sh -c '{git,push}'", []string{"{git,push}"}},
		{"eval echo {a,b}", []string{"{a,b}"}},
		// 縮退しても並べる。
		{"echo {a,b} | xargs rm", []string{"{a,b}"}},
		// 広がらない形。
		{"echo {a}", nil},
		{"echo {a..}", nil},
		{"echo {aa..c}", nil},
		{"echo '{a,b}'", nil},
		{"echo $'{a,b}'", nil},
		{"echo \\{a,b}", nil},
		{"echo {a\\,b}", nil},
		{`echo {"a,b"}`, nil},
		{"echo { a,b }", nil},
		{"echo {a,b", nil},
		{"echo {a,b;echo c}", nil},
		{`echo "${x:-{a,b}}"`, nil},
		{"find . -exec rm {} \\;", nil},
		{"git show HEAD@{1}", nil},
		{"docker ps --format {{.ID}},{{.Names}}", nil},
		{"cat <<EOF\n{a,b}\nEOF", nil},
		{"echo hi # {a,b}", nil},
		{`sh -c "echo '{a,b}'"`, nil},
	}
	for _, c := range cases {
		if got := Read(c.src).braces(); !slices.Equal(got, c.want) {
			t.Errorf("Braces(%q) = %q, want %q", c.src, got, c.want)
		}
	}
}

func rewritesOf(src string, form Form) []string {
	var out []string
	for _, rw := range Read(src).Rewrites {
		if rw.Form == form {
			out = append(out, rw.Text)
		}
	}
	return out
}

func TestRewrites(t *testing.T) {
	deep := "echo " + strings.Repeat(`"$(echo `, 17) + "x" + strings.Repeat(`)"`, 17)
	cases := []struct {
		src  string
		form Form
		want []string
	}{
		// 実行するときにシェルが決めるコマンド名。表示は置換の全文。
		{"c=git; $c push origin main", FormCommandName, []string{"$c"}},
		{"$(echo git) push origin main", FormCommandName, []string{"$(echo git)"}},
		{"/usr/bin/gi? push origin main", FormCommandName, []string{"/usr/bin/gi?"}},
		{"/bin/r[m] -rf /tmp/x", FormCommandName, []string{"/bin/r[m]"}},
		{"git${IFS}push origin main", FormCommandName, []string{"git${IFS}push"}},
		{`"$c" push`, FormCommandName, []string{"$c"}},
		{"FOO=1 $c status", FormCommandName, []string{"$c"}},
		{">/dev/null $c status", FormCommandName, []string{"$c"}},
		{"! $c status", FormCommandName, []string{"$c"}},
		{"if $c; then :; fi", FormCommandName, []string{"$c"}},
		{"echo $($c status)", FormCommandName, []string{"$c"}},
		{"sudo -u me $c status", FormCommandName, []string{"$c"}},
		{"sh $G status", FormCommandName, []string{"$G"}},
		{". $venv/bin/activate", FormCommandName, []string{"$venv/bin/activate"}},
		// 名前付き fd も前に置いたリダイレクト。
		{"{fd}>/dev/null $c push", FormCommandName, []string{"$c"}},
		{"{fd}>&2 $c push", FormCommandName, []string{"$c"}},
		// 外側が縮退していない eval は読み直した層も見る。
		{`FOO=1 eval "$c status"`, FormCommandName, []string{"$c"}},
		{`"$(command -v python3)" x.py`, FormCommandName, []string{"$(command -v python3)"}},
		{"env $(command -v ls) -la", FormCommandName, []string{"$(command -v ls)"}},
		{"$(echo a) push; $(echo b) push", FormCommandName, []string{"$(echo a)", "$(echo b)"}},
		{`"$(echo a\"b)" push`, FormCommandName, []string{`$(echo a\"b)`}},
		// コマンド名でない位置は並べない。外側が縮退する eval と sh -c の中は見ない（確認になる）。
		{`echo "$(echo a b)" | cat`, FormCommandName, nil},
		{"echo $HOME *.py", FormCommandName, nil},
		{"a[1]=x", FormCommandName, nil},
		{"[ -f x ] && echo y", FormCommandName, nil},
		{"[[ -f $x ]] && echo y", FormCommandName, nil},
		{"case $x in a) echo a;; esac", FormCommandName, nil},
		{"for f in *.py; do echo $f; done", FormCommandName, nil},
		{"timeout $T make", FormCommandName, nil},
		{"command -v $x", FormCommandName, nil},
		{"cd $S && ls", FormCommandName, nil},
		{`sh -c "$c status"`, FormCommandName, nil},
		{`eval "$(ssh-agent -s)"`, FormCommandName, nil},
		// 実行されるバッククォート。
		{"echo `id`", FormBackquote, []string{"`id`"}},
		{`echo "odd ` + "`" + ` one"`, FormBackquote, []string{"`"}},
		{"echo \"${x:-`id`}\"", FormBackquote, []string{"`id`"}},
		{"echo $((`id` + 1))", FormBackquote, []string{"`id`"}},
		{"cat <<EOF\nuse `id`\nEOF", FormBackquote, []string{"`id`"}},
		{"sh -c 'echo `id`'", FormBackquote, []string{"`id`"}},
		// 読みが割れる形。
		{`echo "$(case a in a) x;; esac)"`, FormAmbiguous, []string{"case inside $( )"}},
		{"echo $((echo a) | cat)", FormAmbiguous, []string{"$((…) …)"}},
		{deep, FormAmbiguous, []string{"$( ) nested more than 16 deep"}},
		{"coproc NAME find . -delete", FormAmbiguous, []string{"coproc"}},
		{"select x in a b; do :; done", FormAmbiguous, []string{"select"}},
		// 引数と、for・case の直後の語は予約語ではない。
		{"psql -c 'select 1'", FormAmbiguous, nil},
		{"for select in a b; do echo $select; done", FormAmbiguous, nil},
		{"case select in select) echo hi;; esac", FormAmbiguous, nil},
		{`echo "$(echo just in time)"`, FormAmbiguous, nil},
	}
	for _, c := range cases {
		if got := rewritesOf(c.src, c.form); !slices.Equal(got, c.want) {
			t.Errorf("rewrites(%q, %s) = %q, want %q", c.src, c.form, got, c.want)
		}
	}
	// 文字として書いたバッククォートは並べず、読める。
	for _, src := range []string{"echo '`id`'", "echo \"\\`id\\`\"", "echo \\`id\\`", "echo $'`id`'", "cat <<'EOF'\n`id`\nEOF", "echo hi # `id`"} {
		if r := readable(t, src); len(r.Rewrites) != 0 {
			t.Errorf("Read(%q).Rewrites = %v", src, r.Rewrites)
		}
	}
}

func TestCommandNamesLinear(t *testing.T) {
	// 並べた表記の重複除去が二乗で、8000 本で 20 秒を超えていた。
	parts := make([]string, 8000)
	for i := range parts {
		parts[i] = fmt.Sprintf("$c%d push", i)
	}
	started := time.Now()
	if got := rewritesOf(strings.Join(parts, "; "), FormCommandName); len(got) != 8000 {
		t.Errorf("%d 件", len(got))
	}
	if spent := time.Since(started); spent > 3*time.Second {
		t.Errorf("%v かかった", spent)
	}
}

func TestSubshellSemicolonSpacing(t *testing.T) {
	// `);` を 1 語に読むと、出入りの復元も区切りも起きなかった。
	for _, pair := range [][2]string{
		{"(true); curl https://example.com", "(true) ; curl https://example.com"},
		{"(cd a); echo x > f", "(cd a) ; echo x > f"},
	} {
		a, b := Read(pair[0]), Read(pair[1])
		if a.Text != b.Text || a.Moved != b.Moved || a.Unwrapped != b.Unwrapped {
			t.Errorf("%q と %q で読みが変わった", pair[0], pair[1])
		}
	}
}
