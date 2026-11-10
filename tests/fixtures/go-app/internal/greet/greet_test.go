package greet

import "testing"

func TestHello(t *testing.T) {
	if got := Hello("a"); got != "hello, a" {
		t.Fatalf("got %q", got)
	}
}
