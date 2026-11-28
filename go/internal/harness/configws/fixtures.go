package configws

// 雛形のワークスペースに置く設定の見本。

// obj は JSON のオブジェクト。ルールファイルは JSON で書く（JSON は YAML としても読める）。
type Obj = map[string]any

// rule はルール 1 件。key は glob か regex。message が空なら書かない（allow は message を持たない）。
func Rule(id, match, key, pattern, message string) Obj {
	o := Obj{"id": id, "match": match, key: pattern}
	if message != "" {
		o["message"] = message
	}
	return o
}

// ruleset はルールファイルの本文。ask は共通設定だけが使う。
func Ruleset(deny, allow []Obj) Obj {
	o := Obj{"version": 1}
	if deny != nil {
		o["deny"] = deny
	}
	if allow != nil {
		o["allow"] = allow
	}
	return o
}

// with は o の写しに欄を足す・差し替える。
func With(o Obj, kv ...any) Obj {
	out := Obj{}
	for k, v := range o {
		out[k] = v
	}
	for i := 0; i < len(kv); i += 2 {
		out[kv[i].(string)] = kv[i+1]
	}
	return out
}

// plus は rules の前に置いた rule を足した新しい並び。
func Plus(rules []Obj, more ...Obj) []Obj {
	return append(append([]Obj{}, rules...), more...)
}

func DenyOf(o Obj) []Obj  { return o["deny"].([]Obj) }
func AllowOf(o Obj) []Obj { return o["allow"].([]Obj) }

// 共通設定。どのツリーにも適用したい deny と、ワークスペースの allow。
var CommonRules = Ruleset(
	[]Obj{
		Rule("credentials", "Write|Edit", "glob", "*/.env*", "credentials are not edited by the agent."),
		Rule("terraform", "Bash", "glob", "*terraform apply*", "terraform apply is not run by the agent."),
		Rule("guard-approved", "Write|Edit|NotebookEdit", "glob", "*/.ccnavi/approved/*", "guard settings. ask the user."),
	},
	[]Obj{
		Rule("ws-src", "Write|Edit", "glob", "*/src/*", ""),
		Rule("ws-read", "Read", "regex", ".", ""),
	},
)

// ワークスペースの設定。ワークスペースのツリーにだけヒットする。
var OwnRules = Ruleset(
	[]Obj{
		Rule("generated", "Write|Edit", "glob", "*/generated/*", "generated files are rebuilt, not edited."),
		Rule("dropdb", "Bash", "glob", "*dropdb*", "dropdb is not run by the agent."),
	},
	[]Obj{Rule("docs", "Write|Edit", "glob", "*/docs/*", "")},
)

// lib の設定。lib のツリーにだけヒットする。
var LibRules = Ruleset(
	[]Obj{
		Rule("schema", "Write|Edit", "glob", "*/schema/*", "schema is not edited by hand. write a migration."),
		Rule("raw-psql", "Bash", "glob", "*psql*", "psql is not run by the agent."),
	},
	[]Obj{Rule("source", "Write|Edit", "glob", "*/src/*", "")},
)

// app の設定。雛形では置かない（無い設定 = 空）。2 つ目の非空の設定を要るテストだけが置く。
var AppRules = Ruleset(
	[]Obj{
		Rule("kube", "Bash", "glob", "*kubectl*", "kubectl is not run by the agent."),
		Rule("deploy", "Bash", "glob", "*deploy.sh*", "deploy.sh is run by a human."),
	},
	nil,
)

// lib 側の、app の `deploy` と同じ呼び出しにヒットする deny。名前順に並ぶかを見る。
var LibDeploy = Rule("deploy-any", "Bash", "glob", "*deploy*", "deploy is run by a human.")

// 共通設定の `ask`。プロジェクトの設定の `allow` で緩められないことを見る。
var AskVendor = Rule("ask-vendor", "Write|Edit", "glob", "*/vendor/*", "vendor はユーザに確かめてから触る。")

// NotebookEdit を実際の tool_name として通すためのルール。欄は notebook_path。
var NotebookRule = Rule("notebook", "NotebookEdit", "glob", "*/notebooks/*", "ノートはユーザが回す。")

// `{root}` を含む定義。共通設定とプロジェクトの設定の両方に同じものを置いて、置換後の全欄一致を見る。
var RootRule = Rule("root-secret", "Write|Edit", "glob", "{root}/*/secret.txt", "secret.txt は置かない。")

// YAML として不正。閉じていないリスト。
const Broken = "version: 1\ndeny: [\n"

const CommonPhasesText = `version: 1
phases:
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
`

const OwnPhasesText = `version: 1
phases:
  docs:
    kind: work
    title: 文書
    review: false
    scope: ["docs/*"]
`

const LibPhasesText = `version: 1
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

const CommonRiskText = `version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
`

const LibRiskText = `version: 1
levels: {critical: 50}
factors:
  - {id: schema, points: 30, glob: "schema/**", message: スキーマに触った}
`

// どの git プロジェクトルートにも置く、判定の的になるファイル。
var keepFiles = []string{"src/keep.py", "generated/keep.py", "schema/keep.sql", "docs/keep.md"}

// ワークスペースの git が無視するもの。実物の .gitignore と同じ 3 つだけ。
// `/.ccnavi/` を丸ごと無視すると共通設定の 3 本が追跡されず、ワークツリー側の設定ができない。
// それができないと、共通設定のワークツリー側の設定が書けて戻らないという穴を一度も再現できない。
const gitignoreText = "/projects/\n/.claude/worktrees/\n/logs/\n"
