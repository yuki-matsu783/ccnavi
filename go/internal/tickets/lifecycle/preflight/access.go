package preflight

import (
	"encoding/json"
	"strconv"
)

// writeLevel は、GitLab で書き込みに足りるロール（30 の Developer 以上）。
const writeLevel = 30

// UserID は GitLab の `user` の応答の id（数でなければ空）。
func UserID(user []byte) string {
	var v struct {
		ID json.Number `json:"id"`
	}
	if json.Unmarshal(user, &v) != nil {
		return ""
	}
	if _, err := strconv.ParseUint(v.ID.String(), 10, 64); err != nil {
		return ""
	}
	return v.ID.String()
}

// Access は、プロジェクトの応答（GitLab は自分の member の応答も。無ければ nil）から書き込みの権限を判定する。答えは
// ok（足りる）・ng（足りない）・unknown（判定できない）と理由。「足りる」は、リポジトリのロールで書き込めるという
// 意味に限る（トークンの scope・ブランチの保護・API と git が別の資格情報で動いていることは確かめていない）。
func Access(kind string, project, member []byte) (string, string) {
	if kind == "github" {
		var v struct {
			Permissions *struct {
				Push *bool `json:"push"`
			} `json:"permissions"`
		}
		_ = json.Unmarshal(project, &v)
		switch {
		case v.Permissions == nil || v.Permissions.Push == nil:
			return "unknown", "応答に permissions が無い"
		case *v.Permissions.Push:
			return ok, ""
		}
		return ng, "push の権限が無い（permissions.push が false）"
	}
	level, found := memberLevel(member)
	if !found {
		level, found = projectLevel(project)
	}
	switch {
	case !found:
		return "unknown", "ロールを読めなかった"
	case level >= writeLevel:
		return ok, ""
	}
	return ng, "ロールが書き込みに足りない（access_level " + strconv.Itoa(level) + "。書き込みは 30 の Developer 以上）"
}

// role は access_level を持つ応答の一部。
type role struct {
	Level *int `json:"access_level"`
}

// memberLevel は member の応答の access_level。
func memberLevel(member []byte) (int, bool) {
	var v role
	if member == nil || json.Unmarshal(member, &v) != nil || v.Level == nil {
		return 0, false
	}
	return *v.Level, true
}

// projectLevel は、プロジェクトの応答の permissions の project_access と group_access の大きいほう。
func projectLevel(project []byte) (int, bool) {
	var v struct {
		Permissions struct {
			Project *role `json:"project_access"`
			Group   *role `json:"group_access"`
		} `json:"permissions"`
	}
	if json.Unmarshal(project, &v) != nil {
		return 0, false
	}
	best, found := 0, false
	for _, a := range []*role{v.Permissions.Project, v.Permissions.Group} {
		if a != nil && a.Level != nil && (!found || *a.Level > best) {
			best, found = *a.Level, true
		}
	}
	return best, found
}
