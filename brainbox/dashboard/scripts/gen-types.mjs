#!/usr/bin/env node
/**
 * Generate the dashboard's timeline-entry bindings from phantom-contracts.
 *
 * Per DECISIONS D2: no submodule, no package registry. We git-fetch the schema
 * at a pinned tag (see ../contract.version), then emit:
 *   - src/lib/contract/timeline-entry.ts          (TS types, for the type-check gate)
 *   - src/lib/contract/timeline-entry.schema.json (raw schema, for ajv at runtime)
 * Both are committed generated artifacts; regenerating must yield no diff.
 *
 * The generated `AgentEnvelope` interface carries an index signature
 * (`[k: string]: unknown`) because the schema declares no `additionalProperties:
 * false` — reads therefore tolerate unknown fields (v2.x additive changes never
 * break the UI). `status` resolves the `$defs/EnvelopeStatus` enum to a union.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, mkdirSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compile } from 'json-schema-to-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const dashboardRoot = join(here, '..');
const outDir = join(dashboardRoot, 'src', 'lib', 'contract');

/** Parse the KEY=VALUE lines of contract.version (ignoring comments/blanks). */
function readPin() {
  const raw = readFileSync(join(dashboardRoot, 'contract.version'), 'utf8');
  const pin = {};
  for (const line of raw.split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const eq = t.indexOf('=');
    if (eq === -1) continue;
    pin[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
  }
  for (const k of ['TAG', 'REPO', 'SCHEMA_PATH']) {
    if (!pin[k]) throw new Error(`contract.version missing ${k}`);
  }
  return pin;
}

/** Shallow-clone phantom-contracts at the pinned tag and return the schema JSON. */
function fetchSchema(pin) {
  const work = mkdtempSync(join(tmpdir(), 'phantom-contracts-'));
  try {
    execFileSync(
      'git',
      ['clone', '--depth', '1', '--branch', pin.TAG, pin.REPO, work],
      { stdio: ['ignore', 'ignore', 'inherit'] },
    );
    // Fail loudly if the pinned tag drifted from the recorded SHA.
    if (pin.SHA) {
      const head = execFileSync('git', ['-C', work, 'rev-parse', 'HEAD'], {
        encoding: 'utf8',
      }).trim();
      if (head !== pin.SHA) {
        throw new Error(
          `pinned tag ${pin.TAG} now points at ${head}, but contract.version records ${pin.SHA}. ` +
            `Re-pin deliberately (update SHA) rather than silently regenerating.`,
        );
      }
    }
    const schemaText = readFileSync(join(work, pin.SCHEMA_PATH), 'utf8');
    return schemaText;
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

async function main() {
  const pin = readPin();
  const schemaText = fetchSchema(pin);
  const schema = JSON.parse(schemaText);

  const banner =
    `/* eslint-disable */\n` +
    `/**\n` +
    ` * GENERATED — do not edit. Source: phantom-contracts ${pin.SCHEMA_PATH}\n` +
    ` * at pinned tag ${pin.TAG} (${pin.SHA || 'unpinned SHA'}).\n` +
    ` * Regenerate with \`npm run gen:types\`; commit the result.\n` +
    ` */`;

  const ts = await compile(schema, 'AgentEnvelope', {
    bannerComment: banner,
    additionalProperties: true, // keep the open index signature → unknown fields tolerated
    style: { singleQuote: true },
  });

  mkdirSync(outDir, { recursive: true });
  writeFileSync(join(outDir, 'timeline-entry.ts'), ts, 'utf8');
  // Ship the schema verbatim for ajv (pretty-printed, trailing newline) so the
  // committed copy is byte-stable across regenerations.
  writeFileSync(
    join(outDir, 'timeline-entry.schema.json'),
    JSON.stringify(schema, null, 2) + '\n',
    'utf8',
  );

  console.log(`Generated ${join('src/lib/contract', 'timeline-entry.ts')} and .schema.json from ${pin.TAG}`);
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
