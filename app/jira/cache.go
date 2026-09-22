package jira

import (
	"sync"
	"time"
)

type entry[T any] struct {
	val T
	exp time.Time
}

// cache is a small TTL map. Deliberately in-process and unexported: this
// serves one panel's lookups and is not a platform cache primitive.
type cache[T any] struct {
	mu  sync.RWMutex
	ttl time.Duration
	m   map[string]entry[T]
}

func newCache[T any](ttl time.Duration) *cache[T] {
	return &cache[T]{ttl: ttl, m: make(map[string]entry[T])}
}

func (c *cache[T]) get(key string) (T, bool) {
	c.mu.RLock()
	e, ok := c.m[key]
	c.mu.RUnlock()
	var zero T
	if !ok || time.Now().After(e.exp) {
		return zero, false
	}
	return e.val, true
}

func (c *cache[T]) put(key string, v T) {
	c.mu.Lock()
	c.m[key] = entry[T]{val: v, exp: time.Now().Add(c.ttl)}
	c.mu.Unlock()
}
