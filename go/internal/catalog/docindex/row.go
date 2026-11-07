package docindex

import (
	"ccnavi/internal/infra/pyjson"
)

// 索引の 1 行のキー。
const (
	keyConceptID   = "concept_id"
	keyDirectory   = "directory"
	keyFrontmatter = "frontmatter"
	keyMtime       = "mtime"
)

// rowKeys は ccnavi が書いた形の行が持つキー。
var rowKeys = []string{keyConceptID, keyDirectory, keyFrontmatter, keyMtime}

// rootDirectory はツリーのルートの直下のファイルの `directory`。
const rootDirectory = "."

// Row は索引の 1 行。読み直した行は 4 つのキーをこの順に持つ。使い回した行は、ccnavi-index.jsonl に書かれたままの
// 形（キーの順・余分なキー）を保つ。書き戻すときに中身を変えないため。
type Row struct{ obj *pyjson.Object }

func newRow(conceptID, directory string, front *pyjson.Object, mtime string) Row {
	var fm any
	if front != nil {
		fm = front
	}
	return Row{pyjson.NewObject(keyConceptID, conceptID, keyDirectory, directory, keyFrontmatter, fm, keyMtime, mtime)}
}

// ConceptID はツリーのルート（合わせた索引ではワークスペースルート）からの相対パスから `.md` を除いたもの。
func (r Row) ConceptID() string { return r.obj.String(keyConceptID) }

// directory は md の置かれたディレクトリ（ルートの直下は rootDirectory）。
func (r Row) directory() string { return r.obj.String(keyDirectory) }

// Mtime は md の更新時刻（ローカル時刻の `YYYY-MM-DDTHH:MM:SS`）。
func (r Row) Mtime() string { return r.obj.String(keyMtime) }

// Front は frontmatter。無い・読めなかったなら nil。
func (r Row) Front() *pyjson.Object {
	front, _ := r.obj.Value(keyFrontmatter).(*pyjson.Object)
	return front
}

// JSON は出力に書く形。呼び手は書き換えない。
func (r Row) JSON() *pyjson.Object { return r.obj }

// Line は ccnavi-index.jsonl と `--format jsonl` の 1 行（改行なし）。
func (r Row) Line() string {
	line, _ := compact(r.obj)
	return line
}

// Prefixed は、concept_id と directory の頭に prefix を付けた写しを返す。プロジェクトの行を、
// ワークスペースルートから指す形にするため。prefix が空なら r をそのまま返す。
func (r Row) Prefixed(prefix string) Row {
	if prefix == "" {
		return r
	}
	directory := r.directory()
	if directory == "" || directory == rootDirectory {
		directory = prefix
	} else {
		directory = prefix + "/" + directory
	}
	return Row{r.obj.With(keyConceptID, prefix+"/"+r.ConceptID(), keyDirectory, directory)}
}

// compact は json.dumps(v, ensure_ascii=False, separators=(",", ":"))。
func compact(v any) (string, error) {
	return pyjson.Dumps(v, pyjson.Options{RawUnicode: true, Compact: true})
}

// validRow は ccnavi が書いた形の 1 行か。
func validRow(v any) (Row, bool) {
	obj, ok := v.(*pyjson.Object)
	if !ok {
		return Row{}, false
	}
	for _, key := range rowKeys {
		if _, ok := obj.Get(key); !ok {
			return Row{}, false
		}
	}
	for _, key := range []string{keyConceptID, keyDirectory, keyMtime} {
		if _, ok := obj.Value(key).(string); !ok {
			return Row{}, false
		}
	}
	switch obj.Value(keyFrontmatter).(type) {
	case nil, *pyjson.Object:
	default:
		return Row{}, false
	}
	if checkJSON(obj) != nil {
		return Row{}, false
	}
	return Row{obj}, true
}
