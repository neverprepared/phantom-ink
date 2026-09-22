package jira

import (
	"sync"
	"testing"
	"time"
)

func TestCacheHitAndMiss(t *testing.T) {
	c := newCache[string](time.Minute)
	if _, ok := c.get("a"); ok {
		t.Fatal("empty cache returned a hit")
	}
	c.put("a", "value")
	got, ok := c.get("a")
	if !ok || got != "value" {
		t.Fatalf("get = (%q, %v), want (\"value\", true)", got, ok)
	}
}

func TestCacheExpires(t *testing.T) {
	c := newCache[string](time.Millisecond)
	c.put("a", "value")
	time.Sleep(5 * time.Millisecond)
	if _, ok := c.get("a"); ok {
		t.Fatal("expired entry still returned a hit")
	}
}

func TestCacheConcurrent(t *testing.T) {
	// Guards against a map-write race; meaningful only under -race.
	c := newCache[int](time.Minute)
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			c.put("k", i)
			c.get("k")
		}(i)
	}
	wg.Wait()
}
