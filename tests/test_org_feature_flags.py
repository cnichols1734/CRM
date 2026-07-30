"""Tests for per-organization feature overrides.

Covers the rules behind the platform admin toggle UI: overrides are only
stored when they differ from the tier default, killswitched features are left
alone, and unmanaged keys survive a save.

Run with: python -m pytest tests/test_org_feature_flags.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_flags import (
    GLOBAL_FEATURE_OVERRIDES,
    TIER_FEATURES,
    all_feature_names,
    describe_org_features,
    org_has_feature,
    set_org_feature_overrides,
    tier_default_for,
)
from models import Organization, db


@pytest.fixture()
def org(app, seed):
    """A plain pro-tier org with no overrides."""
    with app.app_context():
        record = db.session.get(Organization, seed['org_a'])
        record.feature_flags = {}
        db.session.commit()
        yield record


def _desired(org, **changes):
    """Start from current effective values, then apply changes."""
    current = {row['name']: row['enabled'] for row in describe_org_features(org)}
    current.update(changes)
    return current


class TestOverrideStorage:
    def test_enabling_a_feature_off_by_default_stores_an_override(self, app, org):
        with app.app_context():
            assert tier_default_for('BOB_TELEGRAM', org.subscription_tier) is False

            flags = set_org_feature_overrides(org, _desired(org, BOB_TELEGRAM=True))
            db.session.commit()

            assert flags['BOB_TELEGRAM'] is True
            assert org_has_feature('BOB_TELEGRAM', org) is True

    def test_values_matching_the_tier_default_are_not_stored(self, app, org):
        with app.app_context():
            flags = set_org_feature_overrides(org, _desired(org))
            db.session.commit()

            # Every value equals its tier default, so nothing needs storing.
            assert flags == {}

    def test_turning_an_override_back_to_default_removes_it(self, app, org):
        with app.app_context():
            set_org_feature_overrides(org, _desired(org, BOB_TELEGRAM=True))
            db.session.commit()
            assert 'BOB_TELEGRAM' in org.feature_flags

            set_org_feature_overrides(org, _desired(org, BOB_TELEGRAM=False))
            db.session.commit()

            assert 'BOB_TELEGRAM' not in org.feature_flags
            assert org_has_feature('BOB_TELEGRAM', org) is False

    def test_inherited_features_follow_a_tier_change(self, app, org):
        """The point of not storing matches: tier upgrades still land."""
        with app.app_context():
            org.subscription_tier = 'free'
            set_org_feature_overrides(org, _desired(org))
            db.session.commit()
            assert org_has_feature('TRANSACTIONS', org) is False

            org.subscription_tier = 'pro'
            db.session.commit()

            assert org_has_feature('TRANSACTIONS', org) is True

    def test_disabling_a_feature_the_tier_grants_stores_an_override(self, app, org):
        with app.app_context():
            assert org_has_feature('TRANSACTIONS', org) is True

            flags = set_org_feature_overrides(org, _desired(org, TRANSACTIONS=False))
            db.session.commit()

            assert flags['TRANSACTIONS'] is False
            assert org_has_feature('TRANSACTIONS', org) is False


class TestEdgeCases:
    def test_killswitched_feature_keeps_its_stored_intent(self, app, org):
        """Its toggle ships disabled, so an absent field is not a real 'off'."""
        killswitched = next(iter(GLOBAL_FEATURE_OVERRIDES))
        with app.app_context():
            org.feature_flags = {killswitched: True}
            db.session.commit()

            desired = _desired(org)
            desired.pop(killswitched, None)
            flags = set_org_feature_overrides(org, desired)
            db.session.commit()

            assert flags[killswitched] is True

    def test_unmanaged_keys_survive_a_save(self, app, org):
        with app.app_context():
            org.feature_flags = {'SOME_LEGACY_FLAG': True}
            db.session.commit()

            flags = set_org_feature_overrides(org, _desired(org))
            db.session.commit()

            assert flags['SOME_LEGACY_FLAG'] is True


class TestDescribeOrgFeatures:
    def test_reports_every_known_feature(self, app, org):
        with app.app_context():
            rows = describe_org_features(org)
            assert {row['name'] for row in rows} == set(all_feature_names())
            assert set(TIER_FEATURES['enterprise']) <= {row['name'] for row in rows}

    def test_marks_overrides_and_killswitches(self, app, org):
        killswitched = next(iter(GLOBAL_FEATURE_OVERRIDES))
        with app.app_context():
            set_org_feature_overrides(org, _desired(org, BOB_TELEGRAM=True))
            db.session.commit()

            rows = {row['name']: row for row in describe_org_features(org)}

            assert rows['BOB_TELEGRAM']['overridden'] is True
            assert rows['BOB_TELEGRAM']['enabled'] is True
            assert rows['TRANSACTIONS']['overridden'] is False
            assert rows[killswitched]['locked'] is True
