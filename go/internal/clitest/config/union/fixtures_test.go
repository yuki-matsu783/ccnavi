package union

// 雛形のワークスペースに置く設定の見本。

// obj は JSON のオブジェクト。ルールファイルは JSON で書く（JSON は YAML としても読める）。
type obj = map[string]any

// rule はルール 1 件。key は glob か regex。message が空なら書かない（allow は message を持たない）。
func rule(id, match, key, pattern, message string) obj {
	o := obj{"id": id, "match": match, key: pattern}
	if message != "" {
		o["message"] = message
	}
	return o
}

// ruleset はルールファイルの本文。ask は共通レイヤーだけが使う。
func ruleset(deny, allow []obj) obj {
	o := obj{"version": 1}
	if deny != nil {
		o["deny"] = deny
	}
	if allow != nil {
		o["allow"] = allow
	}
	return o
}

// with は o の写しに欄を足す・差し替える。
func with(o obj, kv ...any) obj {
	out := obj{}
	for k, v := range o {
		out[k] = v
	}
	for i := 0; i < len(kv); i += 2 {
		out[kv[i].(string)] = kv[i+1]
	}
	return out
}

// plus は rules の前に置いた rule を足した新しい並び。
func plus(rules []obj, more ...obj) []obj {
	return append(append([]obj{}, rules...), more...)
}

func denyOf(o obj) []obj  { return o["deny"].([]obj) }
func allowOf(o obj) []obj { return o["allow"].([]obj) }

// 共通レイヤー。どのツリーにも適用したい deny と、ワークスペースの allow。
var commonRules = ruleset(
	[]obj{
		rule("credentials", "Write|Edit", "glob", "*/.env*", "credentials are not edited by the agent."),
		rule("terraform", "Bash", "glob", "*terraform apply*", "terraform apply is not run by the agent."),
		rule("guard-approved", "Write|Edit|NotebookEdit", "glob", "*/.ccnavi/approved/*", "guard settings. ask the user."),
	},
	[]obj{
		rule("ws-src", "Write|Edit", "glob", "*/src/*", ""),
		rule("ws-read", "Read", "regex", ".", ""),
	},
)

// ワークスペース自身のレイヤー。ワークスペースのツリーにだけヒットする。
var ownRules = ruleset(
	[]obj{
		rule("generated", "Write|Edit", "glob", "*/generated/*", "generated files are rebuilt, not edited."),
		rule("dropdb", "Bash", "glob", "*dropdb*", "dropdb is not run by the agent."),
	},
	[]obj{rule("docs", "Write|Edit", "glob", "*/docs/*", "")},
)

// lib のレイヤー。lib のツリーにだけヒットする。
var libRules = ruleset(
	[]obj{
		rule("schema", "Write|Edit", "glob", "*/schema/*", "schema is not edited by hand. write a migration."),
		rule("raw-psql", "Bash", "glob", "*psql*", "psql is not run by the agent."),
	},
	[]obj{rule("source", "Write|Edit", "glob", "*/src/*", "")},
)

// app のレイヤー。雛形では置かない（無いレイヤー = 空）。2 つ目の非空のレイヤーを要るテストだけが置く。
var appRules = ruleset(
	[]obj{
		rule("kube", "Bash", "glob", "*kubectl*", "kubectl is not run by the agent."),
		rule("deploy", "Bash", "glob", "*deploy.sh*", "deploy.sh is run by a human."),
	},
	nil,
)

// lib 側の、app の `deploy` と同じ呼び出しにヒットする deny。名前順に並ぶかを見る。
var libDeploy = rule("deploy-any", "Bash", "glob", "*deploy*", "deploy is run by a human.")

// 共通レイヤーの `ask`。プロジェクトのレイヤーの `allow` で緩められないことを見る。
var askVendor = rule("ask-vendor", "Write|Edit", "glob", "*/vendor/*", "vendor はユーザに確かめてから触る。")

// NotebookEdit を実際の tool_name として通すためのルール。欄は notebook_path。
var notebookRule = rule("notebook", "NotebookEdit", "glob", "*/notebooks/*", "ノートはユーザが回す。")

// `{root}` を含む定義。共通レイヤーとレイヤーの両方に同じものを置いて、置換後の全欄一致を見る。
var rootRule = rule("root-secret", "Write|Edit", "glob", "{root}/*/secret.txt", "secret.txt は置かない。")

// YAML として不正。閉じていないリスト。
const broken = "version: 1\ndeny: [\n"

const commonPhasesText = `version: 1
phases:
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
`

const ownPhasesText = `version: 1
phases:
  docs:
    kind: work
    title: 文書
    review: false
    scope: ["docs/*"]
`

const libPhasesText = `version: 1
phases:
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  build:
    kind: work
    title: ビルド
    review: false
    scope: ["src/*"]
  release:
    kind: work
    title: リリース
    review: true
    scope: ["src/*"]
`

const commonRiskText = `version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
`

const libRiskText = `version: 1
levels: {critical: 50}
factors:
  - {id: schema, points: 30, glob: "schema/**", message: スキーマに触った}
`

// どの git プロジェクトルートにも置く、判定の的になるファイル。
var keepFiles = []string{"src/keep.py", "generated/keep.py", "schema/keep.sql", "docs/keep.md"}

// ワークスペースの git が無視するもの。実物の .gitignore と同じ 3 つだけ。
// `/.ccnavi/` を丸ごと無視すると共通レイヤーの 3 本が追跡されず、ワークツリー側の設定ができない。
// それができないと、共通レイヤーのワークツリー側の設定が書けて戻らないという穴を一度も再現できない。
const gitignoreText = "/projects/\n/.claude/worktrees/\n/logs/\n"
