package audit

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadRecords(t *testing.T) {
	dir := t.TempDir()
	put := func(name, text string) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		return path
	}
	log := put("decisions.jsonl", "{\"decision\": \"deny\"}\n壊れた行\n[1]\r\n")
	put("decisions.1.jsonl", "{\"decision\": \"ask\"}")
	put("decisions.jsonl.bak", "{\"decision\": \"ask\"}\n")
	records, recorded := ReadRecords(log)
	if len(records) != 2 || !recorded || records[0].Value("decision") != "ask" {
		t.Errorf("%v %v", records, recorded)
	}
	if records, recorded := ReadRecords(""); records != nil || recorded {
		t.Error("空のログ")
	}
	if _, recorded := ReadRecords(filepath.Join(dir, "none.jsonl")); recorded {
		t.Error("無いログを記録ありとした")
	}
}
