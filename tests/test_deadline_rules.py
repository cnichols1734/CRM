"""
Tests for deadline rules service.

Phase 1B: basic pack loading and deadline calculation.
Phase 2: business day logic, holidays, dependencies.
"""
import pytest
from datetime import date, timedelta
from services.deadline_rules import DeadlineRulesService


class TestDeadlineRulesService:
    """Test deadline rules service."""

    def test_load_pack(self):
        """Test loading a deadline pack."""
        pack = DeadlineRulesService.load_pack('seller_ctc', 'v1')

        assert pack['pack_key'] == 'seller_ctc'
        assert pack['version'] == 'v1'
        assert 'phases' in pack
        assert 'requirements' in pack
        assert len(pack['phases']) > 0
        assert len(pack['requirements']) > 0

    def test_pack_caching(self):
        """Test that packs are cached."""
        pack1 = DeadlineRulesService.load_pack('seller_ctc', 'v1')
        pack2 = DeadlineRulesService.load_pack('seller_ctc', 'v1')

        # Should be the same object (cached)
        assert pack1 is pack2

    def test_get_requirement_definition(self):
        """Test getting a single requirement definition."""
        req = DeadlineRulesService.get_requirement_definition('seller_ctc', 'earnest_money', 'v1')

        assert req is not None
        assert req['title'] == 'Earnest Money Deposited'
        assert req['phase'] == 'option_period'
        assert 'deadline_rule' in req

    def test_get_nonexistent_requirement(self):
        """Test getting a nonexistent requirement returns None."""
        req = DeadlineRulesService.get_requirement_definition('seller_ctc', 'nonexistent', 'v1')
        assert req is None

    def test_list_phases(self):
        """Test listing phases."""
        phases = DeadlineRulesService.list_phases('seller_ctc', 'v1')

        assert len(phases) == 4
        assert phases[0]['phase_key'] == 'option_period'
        assert phases[1]['phase_key'] == 'due_diligence'
        assert phases[2]['phase_key'] == 'financing'
        assert phases[3]['phase_key'] == 'closing'

    def test_list_requirements_in_phase(self):
        """Test listing requirements in a phase."""
        reqs = DeadlineRulesService.list_requirements_in_phase('seller_ctc', 'option_period', 'v1')

        assert 'earnest_money' in reqs
        assert 'option_fee' in reqs
        assert 'option_period_end' in reqs

    def test_calculate_deadline_calendar_days(self):
        """Test calculating a deadline with calendar days."""
        anchor_date = date(2026, 1, 1)  # Thursday
        deadline_rule = {
            'offset_days': 3,
            'unit': 'calendar'
        }

        result = DeadlineRulesService.calculate_deadline(anchor_date, deadline_rule)

        # 3 calendar days from Jan 1 is Jan 4
        assert result == date(2026, 1, 4)

    def test_calculate_deadline_business_days(self):
        """Test calculating a deadline with business days (skip weekends)."""
        anchor_date = date(2026, 1, 1)  # Thursday
        deadline_rule = {
            'offset_days': 3,
            'unit': 'business'
        }

        result = DeadlineRulesService.calculate_deadline(anchor_date, deadline_rule)

        # Thu → Fri, Mon, Tue = Jan 6
        assert result == date(2026, 1, 6)

    def test_calculate_deadline_negative_offset(self):
        """Test calculating a deadline before the anchor date."""
        anchor_date = date(2026, 1, 10)
        deadline_rule = {
            'offset_days': -3,
            'unit': 'calendar'
        }

        result = DeadlineRulesService.calculate_deadline(anchor_date, deadline_rule)

        # 3 days before Jan 10 is Jan 7
        assert result == date(2026, 1, 7)

    def test_load_lease_tenant_pack(self):
        pack = DeadlineRulesService.load_pack('lease_tenant', 'v1')
        assert pack['pack_key'] == 'lease_tenant'
        tenant_reqs = DeadlineRulesService.requirements_for_side(
            'lease_tenant', 'tenant',
        )
        assert 'collect_pay_stubs' in tenant_reqs
        assert tenant_reqs['collect_pay_stubs'].get('telegram_forbidden') is True
        landlord_reqs = DeadlineRulesService.requirements_for_side(
            'lease_tenant', 'landlord',
        )
        assert 'prepare_listing_docs' in landlord_reqs
        assert 'collect_pay_stubs' not in landlord_reqs

    def test_golden_case_earnest_money(self):
        """Golden test case: earnest money deadline."""
        # Effective date: Jan 1, 2026 (Thursday)
        # Earnest money: 3 business days
        # Expected: Jan 6, 2026 (Tuesday) - skipping weekend

        effective_date = date(2026, 1, 1)
        pack = DeadlineRulesService.load_pack('seller_ctc', 'v1')
        earnest_rule = pack['requirements']['earnest_money']['deadline_rule']

        result = DeadlineRulesService.calculate_deadline(
            effective_date,
            earnest_rule,
            business_day_rules=pack.get('business_day_rules'),
        )

        assert result == date(2026, 1, 6)

    def test_golden_case_option_period(self):
        """Golden test case: option period expiration."""
        # Effective date: Jan 1, 2026 (Thursday)
        # Option period: 7 calendar days
        # Expected: Jan 8, 2026 (Thursday)

        effective_date = date(2026, 1, 1)
        pack = DeadlineRulesService.load_pack('seller_ctc', 'v1')
        option_rule = pack['requirements']['option_period_end']['deadline_rule']

        result = DeadlineRulesService.calculate_deadline(effective_date, option_rule)

        assert result == date(2026, 1, 8)

    def test_golden_case_closing_relative(self):
        """Golden test case: final walkthrough relative to closing."""
        # Closing date: Jan 30, 2026
        # Final walkthrough: 1 day before closing
        # Expected: Jan 29, 2026

        closing_date = date(2026, 1, 30)
        pack = DeadlineRulesService.load_pack('seller_ctc', 'v1')
        walkthrough_rule = pack['requirements']['final_walkthrough']['deadline_rule']

        result = DeadlineRulesService.calculate_deadline(closing_date, walkthrough_rule)

        assert result == date(2026, 1, 29)
