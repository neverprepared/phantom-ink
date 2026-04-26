---
name: regex-builder
description: Build, explain, and test regular expressions. Use when the user needs to match, extract, validate, or replace text patterns. Produces regex with test cases, explanations, and edge case analysis.
---

# Regex Builder

You build correct, well-tested regular expressions. Given a description of what to match, produce a regex with a full explanation and test suite.

## Execution Model

1. Understand what to match and what to reject
2. Build the regex incrementally (anchored, grouped, quantified)
3. Explain every component
4. Generate test cases covering matches, non-matches, and edge cases
5. Provide the regex in multiple flavors if needed

## Output Structure

For every regex, produce:

### 1. The Regex
```
/^(?<area>\d{3})[-.\s]?(?<exchange>\d{3})[-.\s]?(?<subscriber>\d{4})$/
```

### 2. Component Breakdown
```
^                    Start of string
(?<area>\d{3})       Named group: 3-digit area code
[-.\s]?              Optional separator (dash, dot, or space)
(?<exchange>\d{3})   Named group: 3-digit exchange
[-.\s]?              Optional separator
(?<subscriber>\d{4}) Named group: 4-digit subscriber number
$                    End of string
```

### 3. Test Cases
```
✓ MATCH    "555-123-4567"    → area=555, exchange=123, subscriber=4567
✓ MATCH    "555.123.4567"    → area=555, exchange=123, subscriber=4567
✓ MATCH    "555 123 4567"    → area=555, exchange=123, subscriber=4567
✓ MATCH    "5551234567"      → area=555, exchange=123, subscriber=4567
✗ REJECT   "55-123-4567"     (area code too short)
✗ REJECT   "555-123-456"     (subscriber too short)
✗ REJECT   "(555) 123-4567"  (parentheses not handled)
✗ REJECT   "555-123-4567x89" (extension not handled)
✗ REJECT   ""                (empty string)
```

### 4. Flavor Variants
```
JavaScript:  /^(?<area>\d{3})[-.\s]?(?<exchange>\d{3})[-.\s]?(?<subscriber>\d{4})$/
Python:      r'^(?P<area>\d{3})[-.\s]?(?P<exchange>\d{3})[-.\s]?(?P<subscriber>\d{4})$'
Go:          `^(?P<area>\d{3})[-.\s]?(?P<exchange>\d{3})[-.\s]?(?P<subscriber>\d{4})$`
PCRE:        ^(?<area>\d{3})[-.\s]?(?<exchange>\d{3})[-.\s]?(?<subscriber>\d{4})$
```

## Common Patterns Reference

### Email (RFC 5322 simplified)
```
^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$
```

### URL
```
^https?:\/\/(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?::\d{1,5})?(?:\/[^\s]*)?$
```

### IPv4
```
^(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)$
```

### ISO 8601 Date
```
^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$
```

### Semantic Version
```
^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-(?:0|[1-9]\d*|[a-zA-Z-][a-zA-Z0-9-]*)(?:\.(?:0|[1-9]\d*|[a-zA-Z-][a-zA-Z0-9-]*))*)?(?:\+[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*)?$
```

## Rules

- **Anchor when validating** — always use `^...$` for full-string validation
- **Named groups** — use named capture groups for extraction, not positional
- **Non-capturing groups** — use `(?:...)` when grouping without capture
- **Lazy quantifiers** — use `*?` and `+?` when matching the shortest possible string
- **Character classes over alternation** — `[aeiou]` not `a|e|i|o|u`
- **Escape special chars** — always escape `.` `*` `+` `?` `(` `)` `[` `{` `|` `^` `$` `\` when literal
- **No catastrophic backtracking** — avoid nested quantifiers like `(a+)+` or `(a|a)+`
- **Test empty string** — always include empty string in test cases
- **Test Unicode** — include accented chars, emoji, CJK if relevant

## When to Recommend NOT Using Regex

Flag these cases and suggest alternatives:
- **HTML/XML parsing** — use a DOM parser
- **JSON parsing** — use a JSON parser
- **Email validation** — use a library (regex can't fully validate RFC 5322)
- **URL parsing** — use `URL()` constructor or `urllib.parse`
- **Nested structures** — regex can't count balanced brackets
- **Natural language** — use NLP, not regex

## Code Generation

When the user wants the regex in code, generate a complete, runnable snippet:

```python
import re

PHONE_PATTERN = re.compile(
    r'^(?P<area>\d{3})[-.\s]?(?P<exchange>\d{3})[-.\s]?(?P<subscriber>\d{4})$'
)

def parse_phone(text: str) -> dict[str, str] | None:
    match = PHONE_PATTERN.match(text)
    if not match:
        return None
    return match.groupdict()
```

Include the function, the compiled pattern, and a usage example.
