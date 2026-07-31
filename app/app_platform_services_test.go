package main

import "testing"

func TestHostAddrFromPorts(t *testing.T) {
	cases := []struct {
		name, ports, want string
	}{
		{"single", "0.0.0.0:9910->9910/tcp", "localhost:9910"},
		{"loopback-bind", "127.0.0.1:9200->9200/tcp", "localhost:9200"},
		{"remapped", "0.0.0.0:5442->5432/tcp", "localhost:5442"},
		{"multi picks first", "0.0.0.0:9001->9001/tcp, 0.0.0.0:9090->9000/tcp", "localhost:9001"},
		{"skips range, takes single", "127.0.0.1:4900->4900/tcp, 0.0.0.0:21890-21892->21890-21892/tcp", "localhost:4900"},
		{"only exposed not published", "9200/tcp", ""},
		{"empty", "", ""},
	}
	for _, c := range cases {
		if got := hostAddrFromPorts(c.ports); got != c.want {
			t.Errorf("%s: hostAddrFromPorts(%q) = %q, want %q", c.name, c.ports, got, c.want)
		}
	}
}
