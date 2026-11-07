package failsafe

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
)

// bound は前の字の境界。通常の判定の `builtin:credentials` と同じ字の組にする。
const bound = `(?:^|[^\w.,\x01-])`

// minimalDeny は認証情報の形、`.git` の中、操作記録の置き場。通常の判定の `builtin:credentials` が止める形のうち、
// 設定を読まずに言える分を写す。通常より広く止めるもの（`.gnupg`・`id_ecdsa`・`.p12`/`.pfx`・`.git` の中と操作記録の
// 置き場の読み取り・先頭が区切りでない `credentials/`）がある。片方だけ直る事故を避けるため、`builtin:credentials` に
// 形を足したらここも見直す。
var minimalDeny = pyre.MustCompile(
	bound+`\.(env|netrc|npmrc|pypirc|dev\.vars)`+
		`|`+bound+`\.(ssh|aws|gnupg)(?:[\\/]|$)`+
		`|\b(id_rsa|id_ed25519|id_ecdsa)\b`+
		`|`+bound+`credentials(?:[\\/]|\.json)`+
		`|`+bound+`secrets?[\\/]`+
		`|[\w\-\\/]\.(pem|key)\b`+
		`|\.(p12|pfx)(?:\s|$)`+
		`|`+bound+`\.git(?:[\\/]|$)`+
		`|`+bound+`logs[\\/]state[\\/]ops(?:[\\/]|$)`,
	pyre.IgnoreCase,
)

// readFields は読み取りのツールが対象にする欄。Grep の pattern（探す文字列）は見ない。Glob の pattern は探す先
// そのもの（`/home/u/.ssh/*`）なので Glob だけ見る。
var readFields = []string{"file_path", "path", "glob", "notebook_path"}

const globPattern = "pattern"

// pathWord は判定できないときに通すコマンドの引数のパス。`-` で始めない（`wc --files0-from=.env` のようにオプションの
// 値で別のファイルを読ませない）。出力や入力が止まらない `/dev/`・`/proc/` も除く。
const pathWord = `(?!/dev/|/proc/)[^-\s\x00;&|<>()$` + "`" + `*?\[\]{}!"'\\][^\s\x00;&|<>()$` + "`" + `*?\[\]{}!"'\\]*`

// single は判定できないときに通すコマンド。単独で、オプションは取らず、引数は 1 つのパスだけ（pwd と ls は無くてよい）。
var single = pyre.MustCompile(`^(?:pwd|ls(?:\s+`+pathWord+`)?|(?:cat|head|tail|wc)\s+`+pathWord+`)$`, 0)

// status は状態を見る入口（`sh <ルート>/.ccnavi/scripts/ccnavi-ticket.sh status|--help` だけ）。実行するスクリプトは
// このワークスペースのものに固定する（置かれた別のスクリプトを走らせない）。
var status = pyre.MustCompile(`^(?:sh|bash)\s+(?P<script>\S+)\s+(?:status|--help)$`, pyre.IgnoreCase)

// relativeStatus はワークスペースルートから打つ相対の綴り。
var relativeStatus = []string{
	config.DefaultProjectHome + "/" + config.ScriptsDir + "/" + config.TicketWrapper,
	"./" + config.DefaultProjectHome + "/" + config.ScriptsDir + "/" + config.TicketWrapper,
}

// IsSafe は、判定に達せなかった呼び出しのうち通してよいものか。
func IsSafe(in hookio.Input, root string) bool {
	switch {
	case slices.Contains(config.ReportTools, in.ToolName):
		return true
	case slices.Contains(config.ReadTools, in.ToolName):
		return readIsSafe(in, root)
	case slices.Contains(config.ShellTools, in.ToolName):
		return shellIsSafe(in, root)
	}
	return false
}

func readIsSafe(in hookio.Input, root string) bool {
	names := readFields
	if in.ToolName == config.ToolGlob {
		names = append(slices.Clone(readFields), globPattern)
	}
	var found []string
	for _, name := range names {
		if v := in.Field(name); v != "" {
			found = append(found, v)
		}
	}
	// 探す場所を省いた Grep・Glob は cwd を探す（`glob` だけ書いても探す場所は省かれたまま）。cwd が操作記録の
	// 置き場や `.git` の中でない限り、省略は通す。
	omitted := len(found) == 0
	if slices.Contains(config.SearchTools, in.ToolName) {
		omitted = in.Field("path") == ""
	}
	if omitted {
		cwd := in.Cwd
		if cwd == "" {
			wd, err := ospath.Getwd()
			if err != nil {
				return false
			}
			cwd = wd
		}
		found = append(found, cwd)
	}
	for _, p := range found {
		if denied(p, in.Cwd, root) {
			return false
		}
	}
	return true
}

func shellIsSafe(in hookio.Input, root string) bool {
	if in.ToolName != config.ToolBash {
		// PowerShell は shell で読めない。
		return false
	}
	command := ospath.Strip(in.Field("command"))
	if command == "" || strings.ContainsAny(command, "\n\r") {
		return false
	}
	reading := shell.Read(command)
	// 最後まで読めたこと。引用・置換・ヒアドキュメントなどで読み切れなければ通さない。
	if reading.Degraded || len(reading.Rewrites) > 0 {
		return false
	}
	var parts []string
	for _, c := range strings.Split(reading.Text, shell.Sep) {
		if c = ospath.Strip(c); c != "" {
			parts = append(parts, c)
		}
	}
	if len(parts) != 1 || parts[0] != command {
		return false
	}
	if workspaceStatus(command, in.Cwd, root) {
		return !minimalDeny.Search(command)
	}
	if !single.Search(command) || minimalDeny.Search(command) {
		return false
	}
	// 引数のパスは cwd から解いて見る。引数の無い ls は cwd を一覧する。
	words := strings.FieldsFunc(command, ospath.IsSpace)
	target := ""
	if len(words) > 1 {
		target = words[1]
	} else if words[0] == "ls" {
		target = "."
	}
	return target == "" || !denied(target, in.Cwd, root)
}

// workspaceStatus は `sh <このワークスペースの ccnavi-ticket.sh> status|--help` か。
func workspaceStatus(command, cwd, root string) bool {
	m, ok := status.Find(command)
	if !ok || root == "" {
		return false
	}
	script, _ := m.Named("script")
	if slices.Contains(strings.Split(strings.ReplaceAll(script, `\`, "/"), "/"), "..") {
		return false
	}
	if slices.Contains(relativeStatus, script) {
		return cwd != "" && pathcase.Fold(cwd) == pathcase.Fold(root)
	}
	want := ospath.Join(root, config.DefaultProjectHome, config.ScriptsDir, config.TicketWrapper)
	return pathcase.Fold(script) == pathcase.Fold(want)
}

// belowRoot は比べる形にして、ワークスペースのルートより上の祖先を除く。ルートの置き場が `secrets/` や `.aws/` の
// 下にあっても、中の読み取りまで止めないため。ルートの中の名前（`.git` など）は残る。ルートの外はそのまま比べる。
func belowRoot(form, root string) string {
	folded := strings.ReplaceAll(pathcase.Fold(form), `\`, "/")
	if root == "" {
		return folded
	}
	for _, spelled := range []string{root, fsio.FullPath(root, root)} {
		head := strings.TrimRight(strings.ReplaceAll(pathcase.Fold(spelled), `\`, "/"), "/")
		if head != "" && (folded == head || strings.HasPrefix(folded, head+"/")) {
			return folded[len(head):]
		}
	}
	return folded
}

// denied はパスが最低限の deny に当たるか。渡された表記と、cwd から解いた先の両方を見る（相対パス・`..`・symlink を
// 挟んだ読み取りが素通りしないように。解いた先で名前が変わる symlink もあるので、書かれた表記も見る）。
// 解けないパス（NUL を含む）は当たる側に倒す。
func denied(path, cwd, root string) bool {
	if strings.Contains(path, "\x00") || strings.Contains(cwd, "\x00") {
		return true
	}
	for _, form := range []string{path, fsio.FullPath(path, cwd)} {
		if minimalDeny.Search(belowRoot(form, root)) {
			return true
		}
	}
	return false
}
