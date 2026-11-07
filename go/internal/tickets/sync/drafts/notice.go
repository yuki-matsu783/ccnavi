package drafts

import (
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/sync/handoff"
)

const msgCloseNotice = "テンプレートにするなら、`ready` の前に `" + config.Launcher + " --templates --draft %s` で下書きを作る" +
	"（ready で閉じると下書きの材料は退避されて拾えない）。" +
	"下書きはユーザが `ccnavi-user-handoff.sh --root %s` で取り込むか、削除してから ready\n"

// sameShape は計画の形（全体計画とフィードバック計画のステップ）が同じか。取り込み済みかを見るときの比べ方で、
// フローの本文は比べない（一般化でフローの文も直るので、文まで比べると取り込んだ直後でも同じに読めない）。
func sameShape(a, b *templates.Template) bool {
	return slices.EqualFunc(a.Plan, b.Plan, sameItem) && slices.EqualFunc(a.Feedback, b.Feedback, sameItem)
}

func sameItem(a, b templates.Item) bool {
	return a.Type == b.Type && a.Review == b.Review && a.Deferred == b.Deferred && slices.Equal(a.After, b.After)
}

// CloseNotice は親が閉じたときに出す、テンプレート化の案内 1 行（末尾の改行つき）。出さないときは空。
// 出すのは、完了した親（done/）が計画を持ち、その識別子の下書きがまだ無く、同じ計画の形のテンプレートが
// 取り込まれていないときだけ。ready で閉じると材料（done/ と flows/）は退避されて拾えなくなるので、ready の前に出す。
// 読めなければ出さない。
func CloseNotice(conf *config.Settings, root, parent string) string {
	found, children, _ := findParent(conf, root, parent)
	if found == nil {
		return ""
	}
	wanted := handoff.FoldPart(parent + templates.Suffix)
	drafts, _, _ := handoff.Walk(found.TreeRoot)
	for _, f := range drafts {
		parts := splitKey(f.Key)
		if k := handoff.KindOf(f.Key); k != nil && k.ID == handoff.Template && parts[len(parts)-1] == wanted {
			return ""
		}
	}
	t, _ := build(conf, found, children, parent)
	scanned := templates.Load(conf, root)
	for _, view := range append([]*templates.LayerRead{scanned.Common}, scanned.Layers...) {
		for _, e := range view.Entries {
			if e.Template != nil && sameShape(e.Template, t) {
				return ""
			}
		}
	}
	return sprintf(msgCloseNotice, parent, shell.Quote(found.TreeRoot))
}
