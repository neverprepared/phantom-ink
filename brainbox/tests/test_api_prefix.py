"""Tests for API session name extraction and role detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from brainbox.api import _extract_session_name, _extract_role


class TestExtractSessionName:
    def test_developer_prefix(self):
        assert _extract_session_name("developer-myproject") == "myproject"

    def test_researcher_prefix(self):
        assert _extract_session_name("researcher-research1") == "research1"

    def test_performer_prefix(self):
        assert _extract_session_name("performer-deploy1") == "deploy1"

    def test_no_prefix(self):
        assert _extract_session_name("somecontainer") == "somecontainer"

    def test_default_session(self):
        assert _extract_session_name("developer-default") == "default"

    def test_nested_dashes(self):
        assert _extract_session_name("developer-my-long-name") == "my-long-name"

    def test_empty_string(self):
        assert _extract_session_name("") == ""


class TestExtractRole:
    def test_role_from_label(self):
        c = MagicMock()
        c.labels = {"brainbox.role": "researcher"}
        assert _extract_role(c) == "researcher"

    def test_default_when_no_label(self):
        c = MagicMock()
        c.labels = {}
        assert _extract_role(c) == "developer"

    def test_default_when_labels_none(self):
        c = MagicMock()
        c.labels = None
        assert _extract_role(c) == "developer"
