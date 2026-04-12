package main

import "phantom-ink/brainbox"

// ---------------------------------------------------------------------------
// Ollama model management — pass-through to brainbox client
// ---------------------------------------------------------------------------

func (a *App) ListOllamaModels() ([]brainbox.OllamaModel, error) {
	return a.client.ListOllamaModels()
}

func (a *App) PullOllamaModel(name string) (string, error) {
	return a.client.PullOllamaModel(name)
}

func (a *App) DeleteOllamaModel(name string) error {
	return a.client.DeleteOllamaModel(name)
}
