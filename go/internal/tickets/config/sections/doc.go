// Package sections は、提案の本文に求める見出し（ticket-sections.yml の parent と child）を読む。
// 共通レイヤーと、親の project: が指すレイヤーの 2 段で、レイヤーに parent（child）があればその種類の並びを
// 置き換える。組み込みの見出しは持たない（どちらにも無ければ、その種類の見出しは見ない）。
package sections
