import json
from pathlib import Path

import pytest

from ship_invoice_agent.normalization import ChargeAliasResolver


def load_aliases() -> dict[str, list[str]]:
    return json.loads(Path("data/charge_aliases.json").read_text(encoding="utf-8"))


def test_original_evidence_overrides_conflicting_model_name():
    resolver = ChargeAliasResolver(load_aliases(), conflict_policy="review")

    result = resolver.resolve(
        model_name="Terminal Handling Charge (THC)",
        original_name="SHA pay TAR：H/C $50",
        source_text="SHA pay TAR：H/C $50",
    )

    assert result.canonical_name == "Handling Charge"
    assert result.matched_aliases == ["H/C"]
    assert result.evidence_source == "original_charge_item"
    assert result.model_canonical == "Terminal Handling Charge"
    assert result.conflict is True
    assert result.requires_review is True
    assert "MODEL_CHARGE_NAME_CONFLICT" in result.warnings
    assert "MODEL_CHARGE_NAME_OVERRIDDEN" in result.warnings


def test_override_policy_keeps_audit_but_does_not_require_review():
    resolver = ChargeAliasResolver(load_aliases(), conflict_policy="override")

    result = resolver.resolve(
        model_name="Terminal Handling Charge (THC)",
        original_name="H/C",
        source_text=None,
    )

    assert result.canonical_name == "Handling Charge"
    assert result.conflict is True
    assert result.requires_review is False
    assert "MODEL_CHARGE_NAME_CONFLICT" in result.warnings


def test_multiple_aliases_for_same_canonical_are_not_ambiguous():
    resolver = ChargeAliasResolver(load_aliases())

    result = resolver.resolve(
        model_name=None,
        original_name="Handling Charge / H/C",
        source_text=None,
    )

    assert result.canonical_name == "Handling Charge"
    assert result.requires_review is False


def test_different_canonical_aliases_in_one_line_require_review():
    resolver = ChargeAliasResolver(load_aliases())

    result = resolver.resolve(
        model_name=None,
        original_name="H/C and THC",
        source_text=None,
    )

    assert result.requires_review is True
    assert result.warnings == ["AMBIGUOUS_CHARGE_ALIAS"]


def test_alias_matching_uses_token_boundaries():
    resolver = ChargeAliasResolver(load_aliases())

    result = resolver.resolve(
        model_name="THCHARGE",
        original_name="THCHARGE",
        source_text=None,
    )

    assert result.canonical_name == "THCHARGE"
    assert result.warnings == ["UNKNOWN_CHARGE_ALIAS"]


def test_conflicting_alias_configuration_fails_fast():
    with pytest.raises(ValueError, match="maps to both"):
        ChargeAliasResolver(
            {
                "Handling Charge": ["H/C"],
                "Other Charge": ["H/C"],
            }
        )
