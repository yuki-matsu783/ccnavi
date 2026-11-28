// Package sections は、提案の本文に求める見出し（ticket-sections.yml の parent と child）を読む。
// 共通設定と、親の project: が指す設定の 2 段で、設定に parent（child）があればその種類の並びを
// 置き換える。組み込みの見出しは持たない（どちらにも無ければ、その種類の見出しは見ない）。
package sections
