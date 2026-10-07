"""Current-install regression: prop_null must remain a material-free hidden model."""

import unittest

from dota_disabler.constants import INVISIBLE_MODEL
from dota_disabler.domain import Mapping
from dota_disabler.planning.conflicts import resolve_candidates
from dota_disabler.planning.context import PlanningContext


class HiddenModelConflictTests(unittest.TestCase):
    def test_winning_hidden_model_does_not_inherit_visible_material_groups(self):
        hidden = Mapping(
            source=INVISIBLE_MODEL,
            target="models/props_nature/prop_null.vmdl",
            reason="model asset override replaced with inferred default",
        )
        visible = Mapping(
            source="models/heroes/tidehunter/tidehunter.vmdl",
            target=hidden.target,
            reason="wearable replaced with slot default",
            neutralize_model_skin=True,
            required_material_groups=3,
        )
        for candidates in ([hidden, visible], [visible, hidden]):
            with self.subTest(order=[candidate.source for candidate in candidates]):
                context = PlanningContext.create({}, {}, {}, [])
                context.candidates.extend(candidates)
                mappings = resolve_candidates(context)
                self.assertEqual(len(mappings), 1)
                self.assertEqual(mappings[0].source, INVISIBLE_MODEL)
                self.assertEqual(mappings[0].required_material_groups, 1)
                self.assertEqual(context.counters["mapping_conflicts"], 1)

    def test_visible_winner_still_inherits_highest_equipped_skin_requirement(self):
        context = PlanningContext.create({}, {}, {}, [])
        target = "models/items/test/shared.vmdl"
        context.candidates.extend([
            Mapping(
                source="models/heroes/test/default.vmdl",
                target=target,
                reason="entity_model override replaced with entity default",
                required_material_groups=2,
            ),
            Mapping(
                source="models/heroes/test/other.vmdl",
                target=target,
                reason="wearable replaced with slot default",
                neutralize_model_skin=True,
                required_material_groups=4,
            ),
        ])
        winner = resolve_candidates(context)[0]
        self.assertEqual(winner.source, "models/heroes/test/default.vmdl")
        self.assertEqual(winner.required_material_groups, 4)
