// Package contract holds the Go bindings for the timeline-entry envelope,
// GENERATED from neverprepared/phantom-contracts at a pinned tag. It is the one
// place the app's envelope shape is defined — the outbox and every emit* helper
// use these types, so the wire shape cannot drift from the canonical model
// (brainbox AgentEnvelope, DECISIONS.md D1).
//
// Regenerate with `go generate ./internal/contract` (or `just app-contract-gen`).
// That fetches schema/timeline-entry.schema.json from the tag in CONTRACT_TAG,
// writes it here as timeline-entry.schema.json (the source for both the
// conformance test below and the Justfile ajv recipe), and regenerates
// envelope.gen.go. Do not hand-edit envelope.gen.go or the schema file; edit the
// model in brainbox and re-tag phantom-contracts.
package contract

import _ "embed"

//go:generate go run ./gen/main.go

// SchemaJSON is the pinned timeline-entry JSON Schema, embedded so the
// conformance test validates emitted envelopes without a network fetch (CI has
// no access to the private phantom-contracts repo). It is byte-identical to the
// file the Justfile ajv recipe validates against — one source, two consumers.
//
//go:embed timeline-entry.schema.json
var SchemaJSON []byte
