"""Granular weighted-progress behavior."""

from __future__ import annotations

import unittest

from dota_disabler.planning import build_plan
from dota_disabler.progress import WeightedProgress


class WeightedProgressTests(unittest.TestCase):
    def test_named_phases_map_work_and_child_updates_to_fractional_percentages(self):
        updates: list[tuple[float, str]] = []
        progress = WeightedProgress(
            lambda percent, message: updates.append((percent, message)),
            (("first", 1), ("second", 3)),
            minimum_delta=0,
        )

        progress.begin("first", "Starting")
        progress.work("first", 1, 2, "Half of the first phase")
        progress.complete("first", "First phase complete")
        progress.child_callback("second")(50.5, "Halfway through the child")
        progress.complete("second", "Complete")

        percentages = [percent for percent, _message in updates]
        self.assertEqual(percentages, [0, 12.5, 25, 62.875, 100])
        self.assertEqual(percentages, sorted(percentages))

    def test_small_updates_are_throttled_at_one_tenth_of_a_percent(self):
        updates: list[tuple[float, str]] = []
        progress = WeightedProgress(
            lambda percent, message: updates.append((percent, message)),
            (("work", 1),),
        )

        progress.begin("work", "Starting")
        progress.work("work", 1, 2000, "Below the display threshold")
        progress.work("work", 2, 2000, "At the display threshold")

        self.assertEqual([percent for percent, _message in updates], [0, 0.1])

    def test_regressive_child_updates_never_move_the_bar_backward(self):
        updates: list[float] = []
        progress = WeightedProgress(
            lambda percent, _message: updates.append(percent),
            (("work", 1),),
            minimum_delta=0,
        )
        child = progress.child_callback("work")

        child(70, "Ahead")
        child(40, "Stale")

        self.assertEqual(updates, [70, 70])

    def test_invalid_phase_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            WeightedProgress(None, (("work", 0),))
        with self.assertRaisesRegex(ValueError, "unique"):
            WeightedProgress(None, (("work", 1), ("work", 2)))

    def test_planning_reports_its_non_item_finalization_work(self):
        updates: list[tuple[str, int, int]] = []

        build_plan(
            {},
            {},
            {},
            [],
            work_progress=lambda operation, completed, total: updates.append(
                (operation, completed, total)
            ),
        )

        self.assertEqual(
            updates,
            [
                ("global_effects", 1, 4),
                ("model_overrides", 2, 4),
                ("conflicts", 3, 4),
                ("finalize", 4, 4),
            ],
        )


if __name__ == "__main__":
    unittest.main()
