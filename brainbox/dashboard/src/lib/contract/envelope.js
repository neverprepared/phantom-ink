/**
 * Timeline-entry (AgentEnvelope) contract helpers for the dashboard.
 *
 * Types are generated from phantom-contracts at a pinned tag — see
 * ./timeline-entry.ts and ../../../contract.version. This module is the seam
 * between the bus contract and the UI:
 *
 *   - readEnvelope()     — the read path. Passes payloads through unchanged so
 *                          unknown/additive fields (a future v2.x) never break
 *                          rendering. The generated `AgentEnvelope` type carries
 *                          an index signature, so extra keys stay well-typed.
 *   - validateEnvelope() — the write path. Any payload the UI POSTs back to the
 *                          bus MUST pass this (ajv against the pinned schema)
 *                          before send; see assertEmittable().
 *
 * @typedef {import('./timeline-entry.js').AgentEnvelope} AgentEnvelope
 */

import Ajv from 'ajv';
import schema from './timeline-entry.schema.json' with { type: 'json' };

// One compiled validator for the process. `strict: false` because the schema is
// draft-07 with pydantic-flavoured keywords (title/$comment) we don't police;
// we validate structure, not meta-schema purity. allErrors → full error list.
const ajv = new Ajv({ strict: false, allErrors: true });
const _validate = ajv.compile(schema);

/**
 * Narrow an untrusted read payload to the envelope type without stripping
 * unknown fields. This is intentionally a pass-through cast: reads tolerate a
 * superset of the known shape so additive schema changes are non-breaking.
 *
 * @param {unknown} raw
 * @returns {AgentEnvelope}
 */
export function readEnvelope(raw) {
  return /** @type {AgentEnvelope} */ (raw);
}

/**
 * Validate a payload the UI intends to POST back to the bus against the pinned
 * timeline-entry schema.
 *
 * @param {unknown} payload
 * @returns {{ valid: boolean, errors: string[] }}
 */
export function validateEnvelope(payload) {
  const valid = _validate(payload);
  if (valid) return { valid: true, errors: [] };
  const errors = (_validate.errors || []).map(
    (e) => `${e.instancePath || '(root)'} ${e.message}`,
  );
  return { valid: false, errors };
}

/**
 * Guard for emit paths: validate before send, throw on violation so a
 * non-conforming payload never reaches the bus. Returns the payload typed as an
 * envelope so callers can inline it into a fetch body.
 *
 * @param {unknown} payload
 * @returns {AgentEnvelope}
 */
export function assertEmittable(payload) {
  const { valid, errors } = validateEnvelope(payload);
  if (!valid) {
    throw new Error(`envelope failed contract validation: ${errors.join('; ')}`);
  }
  return /** @type {AgentEnvelope} */ (payload);
}
