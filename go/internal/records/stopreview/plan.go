package stopreview

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"slices"
)

// HookMark は登録した agent hook の prompt の先頭行（scripts/ccnavi-stop-review.sh と同じ）。
const HookMark = "[ccnavi-stop-review]"

// judgeAllowRules は判定役の Bash を許す権限。`dontAsk` で動く agent 型 hook の中では、許可が無いと実行できない。
// scripts/ccnavi-stop-review.sh の ALLOW_RULES と同じ。
var judgeAllowRules = []string{
	"Bash(" + config.Launcher + " stop-review material:*)",
	"Bash(" + config.Launcher + " stop-review record:*)",
}

// JudgeAllowRules は judgeAllowRules の写しを返す（呼び手が書き換えても元は変わらない）。
func JudgeAllowRules() []string { return slices.Clone(judgeAllowRules) }

// Final は試行の最終分類。
type Final string

const (
	finalOK         Final = "ok"           // 終了可
	finalFlawed     Final = "flawed"       // 不備検出
	FinalUnchecked  Final = "unchecked"    // 未検査
	FinalOutOfScope Final = "out_of_scope" // 対象外
	FinalSkipped    Final = "skipped"      // 検査省略
)

// Reason は候補にした・しなかった理由。計画に残し、候補でなければ hook が最終分類を選ぶのに使う。
type Reason string

const (
	reasonCandidate          Reason = "candidate"
	ReasonNotMainAgent       Reason = "not_main_agent"
	ReasonStopHookActive     Reason = "stop_hook_active"
	ReasonDisabled           Reason = "disabled"
	ReasonExistingNudge      Reason = "existing_nudge"
	ReasonTargetUnresolvable Reason = "target_unresolvable"
	ReasonApprovedInProgress Reason = "approved_in_progress"
	ReasonCriteriaUnreadable Reason = "criteria_unreadable"
)

// StopTarget は Stop の機会を単位にする基準（R1・R3）の対象。
const StopTarget = "stop"

// Proposal は計画に載せる提案の版 1 つ。
type Proposal struct {
	Key    string // 判定の対象キー（TargetKey・UnsentKey）
	Ident  string
	Route  Route
	Commit string
	Files  []File
}

// Submitted はこのセッションで approval-request した提案と、今の承認状態。
type Submitted struct {
	Ident      string
	Approved   bool
	InProgress bool
}

// Plan は同じ Stop の判定計画。hook が組み、WritePlan が key と attemptID を足して書く。
type Plan struct {
	IsCandidate     bool
	Reason          Reason
	Final           Final // 候補でないとき ccnavi が確定する最終分類。空なら検査省略
	Targets         []string
	Proposals       []Proposal
	NoR2            []string // 本文が無い版（送れなかった提案）。R2 を求めない
	MaterialVersion string
	Refs            []string
	Submitted       []Submitted
	Tainted         bool
	Effective       config.Mode
	Rejudge         bool
	Original        string // 再判定なら元の試行 ID
	ExistingNudge   bool

	key       string
	AttemptID string
}

// Setting は CCNAVI_STOP_REVIEW に全体のモードを掛けた実効値。既定と未知の値は dry-run。
// 既定が dry-run で、読めない値を言わないので、enable に倒す selfguard.Resolve・config.ResolveMode とは分ける（Python も別）。
func Setting(mode config.Mode, declared string) config.Mode {
	value := config.Mode(pyfmt.Lower(ospath.Strip(declared)))
	if value != config.Disable && value != config.DryRun && value != config.Enable {
		value = config.DryRun
	}
	if mode == config.Disable {
		return config.Disable
	}
	return config.EffectiveSetting(mode, value)
}

// CandidateInput は AI 判定の候補にするかを決める材料。
type CandidateInput struct {
	MainAgent          bool
	StopHookActive     bool
	AIContinuation     bool
	alreadyRejudged    bool
	Effective          config.Mode
	TicketsEnabled     bool
	Registered         bool
	ExistingNudge      bool
	targetUnresolvable bool
	ProposalActivity   bool
	ApprovedInProgress bool
}

// Candidate は AI 判定の候補にするかと、その理由。
func Candidate(in CandidateInput) (bool, Reason) {
	switch {
	case !in.MainAgent:
		return false, ReasonNotMainAgent
	case in.StopHookActive && !(in.AIContinuation && !in.alreadyRejudged):
		return false, ReasonStopHookActive
	case in.Effective == config.Disable || !in.TicketsEnabled || !in.Registered:
		return false, ReasonDisabled
	case in.ExistingNudge:
		return false, ReasonExistingNudge
	case in.targetUnresolvable:
		return false, ReasonTargetUnresolvable
	case in.ProposalActivity || !in.ApprovedInProgress:
		return true, reasonCandidate
	}
	return false, ReasonApprovedInProgress
}

// attemptID は共有キーから試行 ID を作る。再判定は別の試行にする。
func attemptID(key string, rejudge bool) string {
	if rejudge {
		return shortKey(key) + "-r"
	}
	return shortKey(key)
}

func (s Store) planPath(key string) string {
	return ospath.Join(s.State, PlanPrefix+shortKey(key)+fsio.JSONExt)
}

// ReadPlan は共有キーの計画。無い・読めない・空なら false。
func (s Store) ReadPlan(key string) (Plan, bool) {
	if s.State == "" || key == "" {
		return Plan{}, false
	}
	obj := readObject(s.planPath(key))
	if obj.Len() == 0 {
		return Plan{}, false
	}
	return parsePlan(obj), true
}

func (p Plan) object() *pyjson.Object {
	proposals := make([]any, 0, len(p.Proposals))
	for _, x := range p.Proposals {
		proposals = append(proposals, x.object())
	}
	submitted := make([]any, 0, len(p.Submitted))
	for _, x := range p.Submitted {
		submitted = append(submitted, pyjson.NewObject("ident", x.Ident, "approved", x.Approved, "in_progress", x.InProgress))
	}
	return pyjson.NewObject(
		"is_candidate", p.IsCandidate,
		"reason", string(p.Reason),
		"final", string(p.Final),
		"targets", list(p.Targets),
		"proposals", proposals,
		"no_r2", list(p.NoR2),
		"material_version", p.MaterialVersion,
		"refs", list(p.Refs),
		"submitted", submitted,
		"tainted", p.Tainted,
		"effective", string(p.Effective),
		"rejudge", p.Rejudge,
		"original", p.Original,
		"existing_nudge", p.ExistingNudge,
		"key", p.key,
		"attempt_id", p.AttemptID,
	)
}

func (x Proposal) object() *pyjson.Object {
	return pyjson.NewObject("key", x.Key, "ident", x.Ident, "route", string(x.Route), "commit", x.Commit, "files", filesList(x.Files))
}

func list(items []string) []any {
	out := make([]any, 0, len(items))
	for _, s := range items {
		out = append(out, s)
	}
	return out
}

func parsePlan(obj *pyjson.Object) Plan {
	p := Plan{
		IsCandidate:     pyfmt.Truthy(obj.Value("is_candidate")),
		Reason:          Reason(obj.String("reason")),
		Final:           Final(obj.String("final")),
		Targets:         strs(obj.Value("targets")),
		NoR2:            strs(obj.Value("no_r2")),
		MaterialVersion: obj.String("material_version"),
		Refs:            strs(obj.Value("refs")),
		Tainted:         pyfmt.Truthy(obj.Value("tainted")),
		Effective:       config.Mode(obj.String("effective")),
		Rejudge:         pyfmt.Truthy(obj.Value("rejudge")),
		Original:        obj.String("original"),
		ExistingNudge:   pyfmt.Truthy(obj.Value("existing_nudge")),
		key:             obj.String("key"),
		AttemptID:       obj.String("attempt_id"),
	}
	items, _ := obj.Value("proposals").([]any)
	for _, item := range items {
		if x, ok := item.(*pyjson.Object); ok {
			p.Proposals = append(p.Proposals, Proposal{
				Key: x.String("key"), Ident: x.String("ident"), Route: Route(x.String("route")),
				Commit: x.String("commit"), Files: parseFiles(x.Value("files")),
			})
		}
	}
	items, _ = obj.Value("submitted").([]any)
	for _, item := range items {
		if x, ok := item.(*pyjson.Object); ok {
			p.Submitted = append(p.Submitted, Submitted{
				Ident: x.String("ident"), Approved: pyfmt.Truthy(x.Value("approved")),
				InProgress: pyfmt.Truthy(x.Value("in_progress")),
			})
		}
	}
	return p
}
