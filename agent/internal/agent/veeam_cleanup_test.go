package agent

import "testing"

func TestSummarizeAndCount(t *testing.T) {
	scan := map[string]any{"items": []any{
		map[string]any{"candidate": true, "size_bytes": float64(1 << 30)},
		map[string]any{"candidate": true, "size_bytes": float64(1 << 30)},
		map[string]any{"candidate": false, "size_bytes": float64(1 << 40)},
	}}
	n, c, b := summarize(scan)
	if n != 3 || c != 2 || b != 2<<30 {
		t.Fatalf("summarize = %d %d %d", n, c, b)
	}
	del := map[string]any{"results": []any{
		map[string]any{"status": "deleted"}, map[string]any{"status": "skipped"},
		map[string]any{"status": "missing"}, map[string]any{"status": "error"},
	}}
	d, s, f := countResults(del)
	if d != 1 || s != 2 || f != 1 {
		t.Fatalf("countResults = %d %d %d", d, s, f)
	}
}
