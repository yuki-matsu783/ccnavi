package mirror

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// 配る判定（scripts/ccnavi-setup.sh）と、親チケットの着手がコピーする判定（processFlag）は同じ答えを返す。
// 食い違うと、着手がコピーする元の SKILL.md が配布先に届かないまま、何の知らせも出ない。sh の側は
// tests/sh/test_setup.py の test_reads_the_process_flag_like_the_ticket_start_does が同じ表で見る。
func TestProcessFlagAgreesWithTheSetupSh(t *testing.T) {
	cases := map[string]struct {
		text string
		want bool
	}{
		"lf":                    {"---\nname: x\nprocess: true\n---\n", true},
		"crlf":                  {"---\r\nname: x\r\nprocess: true\r\n---\r\n", true},
		"padded fences":         {"--- \nprocess: true\n---  \n", true},
		"trailing comment":      {"---\nprocess: true # 工程\n---\n", true},
		"no closing fence":      {"---\nprocess: true\n", false},
		"flag before the fence": {"process: true\n---\nname: x\n---\n", false},
		"bom":                   {"\ufeff---\nprocess: true\n---\n", false},
		"string":                {"---\nprocess: \"true\"\n---\n", false},
		"commented out":         {"---\n# process: true\n---\n", false},
		"in the body":           {"---\nname: x\n---\nprocess: true\n", false},
	}
	for label, c := range cases {
		if got := processFlag([]byte(c.text)); got != c.want {
			t.Errorf("%s: %v, want %v", label, got, c.want)
		}
	}
}

// 配るだけのスキル（distribute: true）は工程スキルではない。ユーザの発言のときの同期の対象に入らない。
func TestADistributeSkillIsNotAProcess(t *testing.T) {
	root := t.TempDir()
	for name, text := range map[string]string{
		"tool":         "---\nname: tool\ndistribute: true\ndescription: 配るだけ\n---\n# tool\n",
		"process-test": "---\nname: process-test\nprocess: true\ndescription: 入口\n---\n# process-test\n",
	} {
		path := filepath.Join(root, ".claude", "skills", name, "SKILL.md")
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if got := processes(root); !reflect.DeepEqual(got, []string{"process-test"}) {
		t.Errorf("processes = %q", got)
	}
}
