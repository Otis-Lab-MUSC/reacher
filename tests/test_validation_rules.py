"""Unit tests for validation_rules.py system prompt content."""

from reacher.api.routers.validation_rules import SYSTEM_PROMPT


class TestSystemPrompt:
    def test_non_empty(self):
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 100

    def test_contains_all_paradigms(self):
        for paradigm in ("fr", "pr", "vi", "omission", "pavlovian"):
            assert paradigm in SYSTEM_PROMPT, f"paradigm '{paradigm}' missing from system prompt"

    def test_json_response_format_instructed(self):
        assert '"valid"' in SYSTEM_PROMPT
        assert '"warnings"' in SYSTEM_PROMPT
        assert '"severity"' in SYSTEM_PROMPT
