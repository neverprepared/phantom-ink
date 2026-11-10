import { describe, it, expect } from 'vitest';
import { tokenize, matchesAll, highlightSegments } from './search';

describe('tokenize', () => {
  it('lowercases and splits on whitespace', () => {
    expect(tokenize('  Phantom   Router ')).toEqual(['phantom', 'router']);
  });

  it('is empty for a blank query', () => {
    expect(tokenize('')).toEqual([]);
    expect(tokenize('   \t ')).toEqual([]);
  });
});

describe('matchesAll', () => {
  const fields = ['neverprepared/phantom-router', 'The orchestrator', '#443'];

  it('matches when every term hits some field', () => {
    expect(matchesAll(['phan', 'rout'], fields)).toBe(true);
    expect(matchesAll(['router', '443'], fields)).toBe(true);
  });

  it('fails when one term hits nothing', () => {
    expect(matchesAll(['router', 'nope'], fields)).toBe(false);
  });

  it('matches everything when there are no terms', () => {
    expect(matchesAll([], fields)).toBe(true);
  });

  it('ignores empty and missing fields', () => {
    expect(matchesAll(['x'], ['', undefined as unknown as string])).toBe(false);
  });
});

describe('highlightSegments', () => {
  it('returns one unmatched segment when there are no terms', () => {
    expect(highlightSegments('phantom-router', [])).toEqual([
      { text: 'phantom-router', match: false },
    ]);
  });

  it('marks a matched run, preserving the source casing', () => {
    expect(highlightSegments('Phantom-Router', ['router'])).toEqual([
      { text: 'Phantom-', match: false },
      { text: 'Router', match: true },
    ]);
  });

  it('marks every occurrence of a term', () => {
    expect(highlightSegments('ab-ab', ['ab'])).toEqual([
      { text: 'ab', match: true },
      { text: '-', match: false },
      { text: 'ab', match: true },
    ]);
  });

  it('merges overlapping and adjacent ranges from different terms', () => {
    expect(highlightSegments('phantom', ['pha', 'ant'])).toEqual([
      { text: 'phant', match: true },
      { text: 'om', match: false },
    ]);
  });

  it('drops terms that do not occur', () => {
    expect(highlightSegments('phantom', ['zzz'])).toEqual([
      { text: 'phantom', match: false },
    ]);
  });

  it('handles an empty string', () => {
    expect(highlightSegments('', ['a'])).toEqual([]);
  });
});
