package ticket

import "ccnavi/internal/infra/yaml"

// Render はチケットを文面に戻す。承認済みチケットを作るときと、スクリプトが欄を書くときに使う。
// 読んだ frontmatter（Raw）をそのまま出すので、コメントと `|` のブロックは残らない（残したい書き換えは fields）。
// Raw に書き出せない型の値があれば yaml.ErrUnrepresentable を包んで返す。
func Render(t *Ticket) (string, error) {
	front, err := yaml.Dump(t.Raw, yaml.DumpOptions{})
	if err != nil {
		return "", err
	}
	return Fence + "\n" + front + Fence + "\n" + t.Body, nil
}
