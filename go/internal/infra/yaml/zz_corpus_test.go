package yaml

import (
	"encoding/json"
	"os"
	"testing"

	"ccnavi/internal/infra/pyfmt"
)

func TestZZCorpus(t *testing.T) {
	path := os.Getenv("YAML_CORPUS")
	if path == "" {
		t.Skip()
	}
	data, _ := os.ReadFile(path)
	var cases []struct{ Doc, Repr, Err string }
	json.Unmarshal(data, &cases)
	bad := 0
	for _, c := range cases {
		load := Load
		if os.Getenv("YAML_NOALIAS") != "" {
			load = LoadRefusingAliases
		}
		v, err := load(c.Doc)
		got, want := "", ""
		if err != nil {
			got = "ERR " + err.Error()
		} else {
			got = pyfmt.Repr(v)
		}
		if c.Err != "" {
			want = "ERR " + c.Err
		} else {
			want = c.Repr
		}
		if got != want {
			bad++
			if bad <= 15 {
				t.Errorf("doc %q\n got %q\nwant %q", c.Doc, got, want)
			}
		}
	}
	t.Logf("%d/%d mismatches", bad, len(cases))
}
