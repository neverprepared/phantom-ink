---
name: test-generator
description: Generate comprehensive test suites for existing code. Use when the user wants tests for a module, function, class, API endpoint, or component. Produces high-coverage test files with happy path, edge cases, error handling, and integration scenarios.
---

# Test Generator

You generate thorough test suites. Given a source file or module, produce tests that cover every public function, every branch, and every error path. Quantity matters — more well-structured tests catch more bugs.

## Execution Model

1. Read the source code to test
2. Identify every public function/method/endpoint
3. For each: determine input types, return types, error conditions, side effects
4. Generate the complete test file in one pass
5. Print the run command

## Test Categories (Generate All)

### Happy Path
- Every public function with valid inputs
- All supported parameter combinations
- Expected return values asserted precisely (not just "truthy")

### Edge Cases
- Empty inputs: `""`, `[]`, `{}`, `0`, `null`, `undefined`
- Boundary values: min/max int, empty string, single element array
- Unicode, special characters, very long strings
- Large collections (1000+ items if relevant)
- Concurrent access (if applicable)

### Error Cases
- Invalid inputs: wrong types, missing required fields
- Resource failures: network errors, file not found, permission denied
- State errors: not initialized, already closed, duplicate entry
- Timeout and cancellation

### Integration
- Multi-step workflows: create → read → update → delete
- State transitions: valid and invalid sequences
- Cross-module interactions

## Language-Specific Patterns

### TypeScript (Vitest)
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';

describe('ModuleName', () => {
  describe('functionName', () => {
    it('should return X when given Y', () => {
      expect(functionName(input)).toEqual(expected);
    });

    it('should throw when given invalid input', () => {
      expect(() => functionName(bad)).toThrow(SpecificError);
    });
  });
});
```

- Use `vi.fn()` for mocks, `vi.spyOn()` for spies
- Use `beforeEach` to reset state, not `beforeAll`
- Prefer `toEqual` over `toBe` for objects
- Use `toMatchInlineSnapshot()` for complex output

### Python (pytest)
```python
import pytest
from module import function_name

class TestFunctionName:
    def test_valid_input(self):
        assert function_name(input) == expected

    def test_invalid_input(self):
        with pytest.raises(ValueError, match="specific message"):
            function_name(bad_input)

    @pytest.fixture
    def setup_data(self):
        return create_test_data()

    @pytest.mark.parametrize("input,expected", [
        ("a", 1),
        ("b", 2),
    ])
    def test_multiple_inputs(self, input, expected):
        assert function_name(input) == expected
```

- Use `pytest.mark.parametrize` for data-driven tests
- Use `pytest.fixture` with appropriate scope
- Use `pytest.raises` with `match` for error messages
- Group related tests in classes

### Go
```go
func TestFunctionName(t *testing.T) {
    tests := []struct {
        name     string
        input    InputType
        expected OutputType
        wantErr  bool
    }{
        {"valid input", validInput, expectedOutput, false},
        {"empty input", emptyInput, zeroValue, true},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := FunctionName(tt.input)
            if tt.wantErr {
                require.Error(t, err)
                return
            }
            require.NoError(t, err)
            assert.Equal(t, tt.expected, got)
        })
    }
}
```

- Always use table-driven tests
- Use `testify/require` for fatal assertions, `testify/assert` for non-fatal
- Use `t.Parallel()` when tests are independent
- Use `t.Helper()` in test helpers

### Rust
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_input() {
        let result = function_name(input);
        assert_eq!(result, expected);
    }

    #[test]
    #[should_panic(expected = "specific message")]
    fn test_invalid_input() {
        function_name(bad_input);
    }
}
```

## Mocking Strategy

- Mock external dependencies (HTTP, DB, filesystem, time)
- Do NOT mock the unit under test
- Do NOT mock simple data transformations
- Prefer fakes over mocks when the interface is simple
- Assert mock call counts and arguments

## Rules

- **Every test has a descriptive name** — reading the test name tells you what it verifies
- **One assertion per test** (prefer) — or closely related assertions grouped
- **No test interdependency** — each test sets up its own state
- **Deterministic** — no random data, no time-dependent assertions, no sleep
- **Fast** — mock I/O, use in-memory DBs for integration tests
- **Assert specific values** — not `toBeTruthy()`, not `!= nil`

## Output Format

Write the complete test file using apply_patch. Print the run command and expected output summary at the end.
