"""Tests for the EventBridge-style pattern matcher (pure, no DB)."""

from __future__ import annotations

from brainbox.event_match import matches, validate_pattern

EVENT = {
    "seq": 42,
    "ts": 1700000000000,
    "id": "hub-task:abc",
    "kind": "event",
    "type": "task.failed",
    "source": "brainbox-hub",
    "status": "failed",
    "title": "run the tests",
    "workspace": "personal",
    "parent_id": None,
    "tags": ["hub-task", "ci"],
    "metadata": {
        "agent_name": "worker-1",
        "attempts": 2,
        "cost_usd": 1.5,
        "nested": {"deep": "value"},
    },
    "outcome": {"ok": False, "error": "boom"},
}


class TestLiterals:
    def test_single_value_matches(self):
        assert matches({"type": ["task.failed"]}, EVENT)

    def test_bare_scalar_normalizes_to_list(self):
        assert matches({"type": "task.failed"}, EVENT)

    def test_or_list(self):
        assert matches({"type": ["task.completed", "task.failed"]}, EVENT)
        assert not matches({"type": ["task.completed", "task.queued"]}, EVENT)

    def test_all_keys_must_match(self):
        assert matches({"type": ["task.failed"], "workspace": ["personal"]}, EVENT)
        assert not matches({"type": ["task.failed"], "workspace": ["gsa"]}, EVENT)

    def test_missing_field_fails(self):
        assert not matches({"nonexistent": ["x"]}, EVENT)

    def test_null_literal_matches_null(self):
        assert matches({"parent_id": [None]}, EVENT)

    def test_no_type_coercion(self):
        assert not matches({"seq": ["42"]}, EVENT)  # string "42" != int 42
        assert matches({"seq": [42]}, EVENT)

    def test_bool_is_not_int(self):
        assert not matches({"metadata": {"attempts": [True]}}, {"metadata": {"attempts": 1}})


class TestArrays:
    def test_event_array_any_semantics(self):
        assert matches({"tags": ["ci"]}, EVENT)
        assert matches({"tags": ["ci", "nope"]}, EVENT)
        assert not matches({"tags": ["nope"]}, EVENT)

    def test_prefix_over_array(self):
        assert matches({"tags": [{"prefix": "hub-"}]}, EVENT)


class TestNesting:
    def test_nested_object_recursion(self):
        assert matches({"metadata": {"agent_name": ["worker-1"]}}, EVENT)
        assert not matches({"metadata": {"agent_name": ["worker-2"]}}, EVENT)

    def test_deep_nesting(self):
        assert matches({"metadata": {"nested": {"deep": ["value"]}}}, EVENT)

    def test_nested_missing_key_fails(self):
        assert not matches({"metadata": {"missing": ["x"]}}, EVENT)


class TestOperators:
    def test_prefix(self):
        assert matches({"type": [{"prefix": "task."}]}, EVENT)
        assert not matches({"type": [{"prefix": "playbook."}]}, EVENT)

    def test_suffix(self):
        assert matches({"type": [{"suffix": ".failed"}]}, EVENT)
        assert not matches({"type": [{"suffix": ".queued"}]}, EVENT)

    def test_prefix_on_non_string_fails(self):
        assert not matches({"seq": [{"prefix": "4"}]}, EVENT)

    def test_exists_true(self):
        assert matches({"workspace": [{"exists": True}]}, EVENT)
        assert not matches({"nonexistent": [{"exists": True}]}, EVENT)

    def test_exists_false(self):
        assert matches({"nonexistent": [{"exists": False}]}, EVENT)
        assert not matches({"workspace": [{"exists": False}]}, EVENT)

    def test_exists_false_on_null_value(self):
        # parent_id is present but null — treated as absent.
        assert matches({"parent_id": [{"exists": False}]}, EVENT)

    def test_absent_field_fails_all_other_matchers(self):
        assert not matches({"nonexistent": [{"prefix": "x"}]}, EVENT)
        assert not matches({"nonexistent": [{"anything-but": ["x"]}]}, EVENT)
        assert not matches({"nonexistent": [{"numeric": [">", 0]}]}, EVENT)

    def test_anything_but(self):
        assert matches({"type": [{"anything-but": ["task.completed"]}]}, EVENT)
        assert not matches({"type": [{"anything-but": ["task.failed"]}]}, EVENT)

    def test_anything_but_bare_scalar(self):
        assert matches({"type": [{"anything-but": "task.completed"}]}, EVENT)

    def test_numeric_single(self):
        assert matches({"metadata": {"cost_usd": [{"numeric": [">", 1.0]}]}}, EVENT)
        assert not matches({"metadata": {"cost_usd": [{"numeric": ["<", 1.0]}]}}, EVENT)

    def test_numeric_range(self):
        assert matches({"metadata": {"attempts": [{"numeric": [">=", 1, "<", 3]}]}}, EVENT)
        assert not matches({"metadata": {"attempts": [{"numeric": [">=", 3, "<", 10]}]}}, EVENT)

    def test_numeric_on_non_numeric_fails(self):
        assert not matches({"type": [{"numeric": [">", 0]}]}, EVENT)

    def test_operator_mixed_with_literal_in_or_list(self):
        assert matches({"type": ["task.completed", {"prefix": "task."}]}, EVENT)


class TestValidatePattern:
    def test_valid(self):
        assert validate_pattern({"type": ["task.failed"]}) == []
        assert validate_pattern({"metadata": {"x": [{"prefix": "a"}]}}) == []

    def test_non_dict_root(self):
        assert validate_pattern(["task.failed"])
        assert validate_pattern("task.failed")
        assert validate_pattern(None)

    def test_empty_pattern(self):
        assert validate_pattern({})

    def test_empty_list(self):
        errs = validate_pattern({"type": []})
        assert any("empty" in e for e in errs)

    def test_bad_exists(self):
        assert validate_pattern({"x": [{"exists": "yes"}]})

    def test_bad_prefix(self):
        assert validate_pattern({"x": [{"prefix": 5}]})

    def test_bad_numeric(self):
        assert validate_pattern({"x": [{"numeric": ["~", 5]}]})
        assert validate_pattern({"x": [{"numeric": [">"]}]})
        assert validate_pattern({"x": [{"numeric": [">", "five"]}]})

    def test_nested_array_element(self):
        assert validate_pattern({"x": [["nested"]]})

    def test_empty_nested_object(self):
        assert validate_pattern({"metadata": {}})
