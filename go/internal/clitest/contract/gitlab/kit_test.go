// 契約テスト（GitLab）の共通の道具。要求の列・模擬・記録の比べ方（contractshape）を、このグループの試験が同じ形で読む。
//
// recorded.sample.json は模擬の応答から作ったので、サンプルとの一致は「模擬が本物に近い」ことを示さない（比較器と要求の列が動くことの
// 確認）。模擬が本物と合っていることを示すのは、本物から録った recorded.json との一致（recorded_test.go）。
package gitlab

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	cs "ccnavi/internal/harness/contractshape"
)

func loadAll(t testing.TB) []cs.Request {
	t.Helper()
	requests, err := cs.LoadAllRequests()
	if err != nil {
		t.Fatal(err)
	}
	return requests
}

func loadFake(t testing.TB) []cs.Request {
	t.Helper()
	requests, err := cs.LoadRequests()
	if err != nil {
		t.Fatal(err)
	}
	return requests
}

func load[T any](t testing.TB, name string) T {
	t.Helper()
	var v T
	if err := cs.LoadData(name, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func loadSample(t testing.TB) cs.Document {
	t.Helper()
	return load[cs.Document](t, "recorded.sample.json")
}

func run(t testing.TB, requests []cs.Request, tamper func(name string, body any) any) map[string]cs.Entry {
	t.Helper()
	out, err := cs.Run(requests, tamper)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// clone は文書を JSON で写す（変異が元の文書を変えないように）。
func clone[T any](t testing.TB, v T) T {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out T
	if err := cs.DecodeJSON(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// dataTexts は契約のデータ（recorded*.json・requests.json）の本文。
func dataTexts(t testing.TB) map[string]string {
	t.Helper()
	entries, err := os.ReadDir(cs.DataDir())
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]string{}
	for _, e := range entries {
		name := e.Name()
		if strings.HasPrefix(name, "recorded") || name == "requests.json" {
			data, err := os.ReadFile(filepath.Join(cs.DataDir(), name))
			if err != nil {
				t.Fatal(err)
			}
			found[name] = string(data)
		}
	}
	return found
}

func diff(key, kind string) cs.Diff { return cs.Diff{Key: key, Kind: kind} }

func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// sameNames は名前の集合が一致するか（並びは問わない）。
func sameNames(t testing.TB, what string, want, got []string) {
	t.Helper()
	want, got = slices.Sorted(slices.Values(want)), slices.Sorted(slices.Values(got))
	if !slices.Equal(want, got) {
		t.Errorf("%s = %v, want %v", what, got, want)
	}
}
