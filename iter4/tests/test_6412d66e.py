# Test Writer Output

```python
"""
Tests for Story: Relax sprint-cut lock to check in-sprint terminal states only

Acceptance criteria:
  - sprint_cut(n) reads the LATEST prior sprint_cut entry's in_sprint_story_ids
  - If any story in that set is in state in_progress or in_review, raise
    SprintCutLockedError
  - If all are in {accepted, rejected, force_closed}, allow the new cut to proceed
  - Existing lock test (test_sprint_cut_locked_rejects) passes with updated semantics
"""

import pytest
from pathlib import Path
import json
import tempfile
import sys

# Assume sm module is importable
import sm


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_log(tmp_path):
    """Provide a temporary log file for hermetic test runs."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    original_log_path = sm.LOG_PATH
    sm.LOG_PATH = log_file
    yield log_file
    sm.LOG_PATH = original_log_path


@pytest.fixture
def active_iteration_with_backlog(temp_log):
    """Set up an active iteration with a story backlog ready for sprint cutting."""
    # iteration_open
    iter_entry = sm.build_entry(
        "iteration_open",
        {
            "iteration_id": "iter-123",
            "requirements": [
                {"requirement_id": "req-1"},
                {"requirement_id": "req-2"},
            ],
        },
    )
    sm._append_entry(iter_entry)

    # story_backlog with 3 stories
    backlog_entry = sm.build_entry(
        "story_backlog",
        {
            "stories": [
                {
                    "story_id": "story-1",
                    "sequence": 1,
                    "title": "Story 1",
                    "size": "M",
                    "requirement_ids": ["req-1"],
                    "acceptance_criteria": "Do thing",
                },
                {
                    "story_id": "story-2",
                    "sequence": 2,
                    "title": "Story 2",
                    "size": "M",
                    "requirement_ids": ["req-2"],
                    "acceptance_criteria": "Do thing",
                },
                {
                    "story_id": "story-3",
                    "sequence": 3,
                    "title": "Story 3",
                    "size": "S",
                    "requirement_ids": ["req-1"],
                    "acceptance_criteria": "Do thing",
                },
            ],
            "role_spec_path": "/path/to/spec",
            "role_spec_hash": "abc123",
        },
    )
    sm._append_entry(backlog_entry)
    yield temp_log


def _transition_story(story_id, from_state, to_state):
    """Helper to write a story_state_change entry."""
    entry = sm.build_entry(
        "story_state_change",
        {
            "story_id": story_id,
            "from_state": from_state,
            "to_state": to_state,
            "notes": f"test transition {from_state} -> {to_state}",
        },
    )
    sm._append_entry(entry)


# ============================================================================
# ACCEPTANCE CRITERIA TESTS
# ============================================================================

class TestSprintCutLockRelaxation:
    """Tests for the relaxed sprint-cut lock that only checks in-progress/in-review."""

    # ========================================================================
    # AC 1: Lock check only applies to LATEST prior sprint_cut in_sprint_story_ids
    # ========================================================================

    def test_lock_reads_latest_prior_sprint_cut(self, active_iteration_with_backlog):
        """Verify that the lock check reads the LATEST sprint_cut entry, not the
        first one."""
        # First sprint cut: stories 1-2 in sprint
        cut1 = sm.sprint_cut(2)
        assert cut1["cut_position"] == 2
        in_sprint_1 = cut1["in_sprint_story_ids"]
        assert in_sprint_1 == ["story-1", "story-2"]

        # Transition story-1 to in_progress (would lock the old cut, but we
        # have a new cut to write)
        _transition_story("story-1", "planned", "in_progress")

        # Second sprint cut: stories 1-3 in sprint (includes story-1 which is
        # in_progress). This should succeed because we're reading the LATEST
        # in_sprint_ids from the SECOND sprint_cut, not the first.
        cut2 = sm.sprint_cut(3)
        assert cut2["cut_position"] == 3
        in_sprint_2 = cut2["in_sprint_story_ids"]
        assert in_sprint_2 == ["story-1", "story-2", "story-3"]

    # ========================================================================
    # AC 2: Lock fires if ANY in-sprint story is in_progress or in_review
    # ========================================================================

    def test_lock_fires_if_any_story_in_progress(self, active_iteration_with_backlog):
        """Lock fires when one of the in-sprint stories is in_progress."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Move story-1 to in_progress
        _transition_story("story-1", "planned", "in_progress")

        # Attempt re-cut should raise SprintCutLockedError
        with pytest.raises(sm.SprintCutLockedError) as exc_info:
            sm.sprint_cut(3)
        assert "locked" in str(exc_info.value).lower()
        assert "story-1" in str(exc_info.value)

    def test_lock_fires_if_any_story_in_review(self, active_iteration_with_backlog):
        """Lock fires when one of the in-sprint stories is in_review."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Move story-2 through to in_review
        _transition_story("story-2", "planned", "in_progress")
        _transition_story("story-2", "in_progress", "in_review")

        # Attempt re-cut should raise SprintCutLockedError
        with pytest.raises(sm.SprintCutLockedError) as exc_info:
            sm.sprint_cut(3)
        assert "locked" in str(exc_info.value).lower()
        assert "story-2" in str(exc_info.value)

    def test_lock_fires_if_multiple_stories_non_terminal(
        self, active_iteration_with_backlog
    ):
        """Lock fires and names all offending stories."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Move both to non-terminal states
        _transition_story("story-1", "planned", "in_progress")
        _transition_story("story-2", "planned", "in_progress")

        with pytest.raises(sm.SprintCutLockedError) as exc_info:
            sm.sprint_cut(3)
        error_msg = str(exc_info.value)
        assert "story-1" in error_msg
        assert "story-2" in error_msg

    # ========================================================================
    # AC 3: Allow re-cut if all in-sprint stories are in terminal states
    # ========================================================================

    def test_lock_allows_recut_if_all_stories_accepted(
        self, active_iteration_with_backlog
    ):
        """Re-cut allowed when all in-sprint stories are accepted."""
        # First cut: stories 1-2 in sprint
        cut1 = sm.sprint_cut(2)
        assert cut1["cut_position"] == 2

        # Move both stories through to accepted
        _transition_story("story-1", "planned", "in_progress")
        _transition_story("story-1", "in_progress", "in_review")
        _transition_story("story-1", "in_review", "accepted")

        _transition_story("story-2", "planned", "in_progress")
        _transition_story("story-2", "in_progress", "in_review")
        _transition_story("story-2", "in_review", "accepted")

        # Re-cut should succeed
        cut2 = sm.sprint_cut(3)
        assert cut2["cut_position"] == 3
        assert cut2["in_sprint_story_ids"] == ["story-1", "story-2", "story-3"]

    def test_lock_allows_recut_if_all_stories_rejected(
        self, active_iteration_with_backlog
    ):
        """Re-cut allowed when all in-sprint stories are rejected."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Move both stories through to rejected
        _transition_story("story-1", "planned", "in_progress")
        _transition_story("story-1", "in_progress", "in_review")
        _transition_story("story-1", "in_review", "rejected")

        _transition_story("story-2", "planned", "in_progress")
        _transition_story("story-2", "in_progress", "in_review")
        _transition_story("story-2", "in_review", "rejected")

        # Re-cut should succeed
        cut2 = sm.sprint_cut(3)
        assert cut2["cut_position"] == 3

    def test_lock_allows_recut_if_all_stories_force_closed(
        self, active_iteration_with_backlog
    ):
        """Re-cut allowed when all in-sprint stories are force_closed."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Force-close both stories
        _transition_story("story-1", "planned", "force_closed")
        _transition_story("story-2", "planned", "force_closed")

        # Re-cut should succeed
        cut2 = sm.sprint_cut(3)
        assert cut2["cut_position"] == 3

    def test_lock_allows_recut_if_mixed_terminal_states(
        self, active_iteration_with_backlog
    ):
        """Re-cut allowed when all in-sprint stories are in terminal states,
        even if they differ (some accepted, some rejected, some force_closed)."""
        # First cut: stories 1-2 in sprint
        sm.sprint_cut(2)

        # Move stories to different terminal states
        _transition_story("story-1", "planned", "in_progress")
        _transition_story("story-1", "in_progress", "in_review")
        _transition_story("story-1", "in_review", "accepted")

        _transition_story("story-2", "planned", "force_closed")

        # Re-cut should succeed
        cut2 = sm.sprint_cut(3)
        assert cut2["cut_position"] == 3

    # ========================================================================
    # AC 4: Already-terminal stories in planned state don't block re-cut
    # ========================================================================

    def test_lock_does_not_fire_for_planned_stories_not_in_sprint(
        self, active_iteration_with_backlog
    ):
        """Stories deferred (not in sprint) remain in planned state; they don't
        block re-cutting."""
        # First cut: stories 1-2 in sprint; story-3 is deferred (planned)
        sm.sprint_cut(2)

        # Transition in-sprint stories to terminal
        _transition_story("story-1", "planned", "in_progress")
        _transition_story("story-1", "in_progress", "in_review")
        _transition_story("story-1", "in_review", "accepted")

        _transition_story("story-2", "planned", "in_progress")
        _transition_story("story-2", "in_progress", "in_review")
        _transition_story("story-2", "in_review", "accepted")

        # story-3 remains in planned state (not in sprint, not affected by lock)
        # Re-cut should succeed
        cut2 = sm.sprint_cut(1)
        assert cut2["cut_position"] == 1

    # ========================================================================
    # AC 5: First sprint_cut (no prior cut) always succeeds
    # ========================================================================

    def test_first_sprint_cut_always_succeeds(self, active_iteration_with_backlog):
        """The first sprint_cut (no prior cut to lock against) always succeeds."""
        cut = sm.sprint_cut(2)
        assert cut["cut_position"] == 2
        assert cut["in_sprint_story_ids"] == ["story-1", "story-2"]

    # ========================================================================
    # AC 6: Log byte-for-byte unchanged on lock failure
    # ========================================================================

    def test_lock_failure_leaves_log_unchanged(self, active_iteration_with_backlog):
        """When lock fires, no new log entry is written."""
        # First cut
        sm.sprint_cut(2)

        # Transition a story to in_progress to arm the lock
        _transition_story("story-1", "planned", "in_progress")

        # Read the log before the failed re-cut
        entries_before = list(sm.read_entries())
        log_size_before = sm.LOG_PATH.stat().st_size

        # Attempt re-cut (should fail)
        with pytest.raises(sm.SprintCutLockedError):
            sm.sprint_cut(3)

        # Verify log is unchanged
        entries_after = list(sm.read_entries())
        log_size_after = sm.LOG_PATH.stat().st_size

        assert len(entries_before) == len(entries_after)
        assert log_size_before == log_size_after

    # ========================================================================
    # BACKWARDS COMPATIBILITY: Existing lock test still passes
    # ========================================================================

    def test_sprint_cut_locked_rejects_with_updated_semantics(
        self, active_iteration_with_backlog
    ):
        """The original lock test (test_sprint_cut_locked_rejects) behavior is
        preserved: if any in-sprint story is not in a terminal state, lock
        fires. With the relaxation, only in_progress/in_review block; planned
        and terminal states (accepted/rejected/force_closed) allow."""
        # First cut: all 3 stories
        sm.sprint_cut(3)

        # Transition story-1 to in_progress
        _transition_story("story-1", "planned", "in_progress")

        # Lock should fire
        with pytest.raises(sm.SprintCutLockedError):
            sm.sprint_cut(2)

        # Now transition to in_review
        _transition_story("story-1", "in_progress", "in_review")

        # Lock should still fire
        with pytest.raises(sm.SprintCutLockedError):
            sm.sprint_cut(2)

        # Now transition to accepted
        _transition_story("story-1", "in_review", "accepted")

        # Lock should NOT fire now
        cut = sm.sprint_cut(2)
        assert cut["cut_position"] == 2


# ============================================================================
# EDGE CASES
# ============================================================================

class TestSprintCutLockEdgeCases:
    """Edge cases and boundary conditions for the lock mechanism."""

    def test_lock_with_empty_prior_in_sprint_list(self, active_iteration_with_backlog):
        """Prior sprint_cut with empty in_sprint_story_ids doesn't lock."""
        # Create a sprint cut with no stories (edge case, but legal)
        # Actually, sprint_cut(0) would fail range validation, but let's
        # manually inject a cut with empty in_sprint_list
        sm.sprint_cut(1)

        # Manually write a degenerate sprint_cut entry with empty in_sprint_ids
        degenerate_cut = sm.build_entry(
            "sprint_cut",
            {
                "cut_position": 0,
                "in_sprint_story_ids": [],
                "de