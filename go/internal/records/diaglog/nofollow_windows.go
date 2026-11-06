//go:build windows

package diaglog

// noFollow は Windows に無い。書き込み先は lstat で先に見る。
const noFollow = 0
