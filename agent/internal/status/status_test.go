package status

import (
	"path/filepath"
	"testing"
	"time"
)

func TestWriteRead(t *testing.T) {
	p := filepath.Join(t.TempDir(), "sub", "status.json")
	in := Status{Version: "0.2.0", State: "approved", Server: "https://x", LastContact: time.Now().UTC().Truncate(time.Second)}
	if err := Write(p, in); err != nil {
		t.Fatal(err)
	}
	out, err := Read(p)
	if err != nil || out.State != "approved" || !out.LastContact.Equal(in.LastContact) {
		t.Fatalf("%+v %v", out, err)
	}
	if err := Write("", in); err != nil {
		t.Fatal("path vuoto deve essere un no-op")
	}
}
