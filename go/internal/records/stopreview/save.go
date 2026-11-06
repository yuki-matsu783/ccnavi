package stopreview

import (
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/redact"
)

// SaveMaterial は判定に渡した材料を、伏せ字にして試行ごとに保存し、伏せ字で変わった箇所の数を返す。
// 判定役には伏せる前の材料を渡し、残すのは伏せた後の材料（再評価のとき、本文が消えていても同じ材料で
// 比べられるように）。置き場を作れない・書けなければ -1（判定は止めない）。
func SaveMaterial(logDir, attemptID, criteria, rendered string) int {
	shown := redact.Redact(rendered)
	changed := max(strings.Count(shown, redact.Stars)-strings.Count(rendered, redact.Stars), 0)
	row := pyjson.NewObject(
		"attempt_id", attemptID,
		"extractor", extractorVersion,
		"criteria_version", digest(criteria),
		"criteria", redact.Redact(criteria),
		"material", shown,
		"redactions", changed,
	)
	path := fsio.RecordPath(logPath(logDir, MaterialDir), "", attemptID, fsio.JSONExt)
	if fsio.MakeDirs(ospath.Dirname(path)) != nil {
		return -1
	}
	if fsio.WriteJSON(path, row) != "" {
		return -1
	}
	return changed
}
