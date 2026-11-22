package hoststub

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"
)

// Getenv は環境変数を引く関数。teststub は os.Getenv を渡し、テストは表を渡す。
type Getenv func(string) string

// Curl は teststub の curl（PATH に置く curl のスタブの中身）。routes.json（FAKE_ROUTES）の `<METHOD> <URL>` で答え、要求を FAKE_LOG に書く。
// 状態が 400 以上なら標準エラーに curl の書き方で言って 22 で終わる。表に無い要求は 404。FAKE_SLEEP があればその秒だけ待ってから答える。
func Curl(args []string, stdin io.Reader, stdout, stderr io.Writer, getenv Getenv) int {
	method, url, data := "GET", "", false
	for i := 0; i < len(args); {
		switch a := args[i]; {
		case a == "-X" || a == "-H" || a == "--header" || a == "--connect-timeout" || a == "--max-time" || a == "--data-binary":
			if a == "-X" && i+1 < len(args) {
				method = args[i+1]
			}
			if a == "--data-binary" {
				data = true
			}
			i += 2
		default:
			if strings.HasPrefix(a, "http") {
				url = a
			}
			i++
		}
	}
	body := ""
	if data {
		raw, _ := io.ReadAll(stdin)
		body = string(raw)
	}
	var log strings.Builder
	fmt.Fprintf(&log, "%s %s\n", method, url)
	if body != "" {
		log.WriteString("BODY " + strings.Join(strings.Split(body, "\n"), " ") + "\n")
	}
	if err := appendFile(getenv("FAKE_LOG"), log.String()); err != nil {
		fmt.Fprintln(stderr, "curl stub:", err)
		return 1
	}
	if sleep := getenv("FAKE_SLEEP"); sleep != "" {
		if secs, err := strconv.ParseFloat(sleep, 64); err == nil {
			time.Sleep(time.Duration(secs * float64(time.Second)))
		}
	}
	raw, err := os.ReadFile(getenv("FAKE_ROUTES"))
	if err != nil {
		fmt.Fprintln(stderr, "curl stub:", err)
		return 1
	}
	var routes Routes
	if err := json.Unmarshal(raw, &routes); err != nil {
		fmt.Fprintln(stderr, "curl stub: routes.json を読めない:", err)
		return 1
	}
	route, ok := routes[method+" "+url]
	if !ok {
		route = Route{Status: 404, Body: map[string]any{"message": "Not Found"}}
	}
	if route.Status >= 400 {
		fmt.Fprintf(stderr, "curl: (22) The requested URL returned error: %d\n", route.Status)
		return 22
	}
	out, _ := json.Marshal(route.Body)
	_, _ = stdout.Write(out)
	return 0
}

func appendFile(path, text string) error {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.WriteString(text)
	return err
}
