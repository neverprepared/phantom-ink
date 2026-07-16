/**
 * Contract smoke test for the dashboard's envelope helpers. Run with
 * `npm run test:contract` (node --test). Exercises the two acceptance-relevant
 * behaviours: reads tolerate unknown fields, and emits are ajv-validated.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  readEnvelope,
  validateEnvelope,
  assertEmittable,
} from '../src/lib/contract/envelope.js';

test('readEnvelope tolerates unknown/additive fields (forward-compat)', () => {
  const withExtra = {
    id: 'e1',
    title: 'Deploy',
    // a hypothetical v2.x additive field the current schema does not name:
    severity: 'high',
    metadata: { k: 'v' },
  };
  const read = readEnvelope(withExtra);
  // Pass-through: nothing stripped, no throw — rendering never breaks on extras.
  assert.equal(read.id, 'e1');
  assert.equal(read.severity, 'high');
  assert.deepEqual(read, withExtra);
});

test('validateEnvelope accepts a minimal valid envelope', () => {
  const { valid, errors } = validateEnvelope({ id: 'e1', title: 'ok' });
  assert.equal(valid, true, errors.join('; '));
});

test('validateEnvelope accepts unknown extra fields on emit (open schema)', () => {
  // The schema has no additionalProperties:false, so additive fields validate.
  const { valid } = validateEnvelope({ id: 'e1', title: 'ok', future_field: 1 });
  assert.equal(valid, true);
});

test('validateEnvelope rejects an envelope missing required fields', () => {
  const { valid, errors } = validateEnvelope({ title: 'no id' });
  assert.equal(valid, false);
  assert.ok(errors.length > 0);
});

test('validateEnvelope rejects a bad status enum value', () => {
  const { valid } = validateEnvelope({ id: 'e1', title: 'x', status: 'bogus' });
  assert.equal(valid, false);
});

test('assertEmittable throws on a non-conforming payload', () => {
  assert.throws(() => assertEmittable({ title: 'no id' }), /contract validation/);
});

test('assertEmittable returns the payload when valid', () => {
  const payload = { id: 'e1', title: 'ok', status: 'active' };
  assert.equal(assertEmittable(payload), payload);
});
