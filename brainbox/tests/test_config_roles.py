"""Tests for role-based configuration (resolved_image, resolved_prefix)."""

from __future__ import annotations

import os
from unittest.mock import patch

from brainbox.config import Settings


class TestResolvedImage:
    def test_default_developer(self):
        s = Settings(role="developer")
        assert s.resolved_image == "ghcr.io/neverprepared/brainbox:latest"

    def test_researcher(self):
        s = Settings(role="researcher")
        assert s.resolved_image == "ghcr.io/neverprepared/brainbox:latest"

    def test_performer(self):
        s = Settings(role="performer")
        assert s.resolved_image == "ghcr.io/neverprepared/brainbox:latest"

    def test_explicit_image_overrides_role(self):
        s = Settings(role="developer", image="my-custom-image")
        assert s.resolved_image == "my-custom-image"

    def test_empty_image_falls_back_to_unified(self):
        s = Settings(role="researcher", image="")
        assert s.resolved_image == "ghcr.io/neverprepared/brainbox:latest"


class TestResolvedPrefix:
    def test_default_developer(self):
        s = Settings(role="developer")
        assert s.resolved_prefix == "developer-"

    def test_researcher(self):
        s = Settings(role="researcher")
        assert s.resolved_prefix == "researcher-"

    def test_performer(self):
        s = Settings(role="performer")
        assert s.resolved_prefix == "performer-"

    def test_explicit_prefix_overrides_role(self):
        s = Settings(role="developer", container_prefix="custom-")
        assert s.resolved_prefix == "custom-"

    def test_empty_prefix_falls_back_to_role(self):
        s = Settings(role="performer", container_prefix="")
        assert s.resolved_prefix == "performer-"


class TestRoleDefault:
    def test_default_is_developer(self):
        s = Settings()
        assert s.role == "developer"

    def test_env_override(self):
        with patch.dict(os.environ, {"CL_ROLE": "researcher"}):
            s = Settings()
            assert s.role == "researcher"
