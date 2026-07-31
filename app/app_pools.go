package main

import "phantom-ink/brainbox"

// Machine-class routing pools (fleet, via the router). The Runners panel shows
// pools + which runners match (tags ⊇ match_tags) and lets the operator manage
// them.

// ListPools returns the routing pools.
func (a *App) ListPools() ([]brainbox.Pool, error) {
	return a.client.ListPools()
}

// UpsertPool creates or updates a pool.
func (a *App) UpsertPool(name string, matchTags []string, policy string) (brainbox.Pool, error) {
	return a.client.UpsertPool(name, matchTags, policy)
}

// DeletePool removes a pool.
func (a *App) DeletePool(name string) error {
	return a.client.DeletePool(name)
}
