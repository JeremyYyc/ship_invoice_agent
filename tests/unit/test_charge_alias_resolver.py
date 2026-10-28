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


@pytest.mark.parametrize("name", ["Wharfage", "WHARFAGE", "wharfage", "WhArFaGe"])
def test_wharfage_case_variants_resolve_to_standard_name(name):
    result = ChargeAliasResolver(load_aliases()).resolve(
        model_name=name,
        original_name=name,
        source_text=f"{name} | 1 | 21.90 | USD | 21.90",
    )

    assert result.canonical_name == "Wharfage"
    assert result.evidence_source == "original_charge_item"
    assert result.model_canonical == "Wharfage"
    assert not result.conflict
    assert not result.requires_review
    assert result.warnings == []


@pytest.mark.parametrize(
    "name",
    [
        "Wharfage Administration Fee",
        "WHARFAGE ADMINISTRATION FEE",
        "Wharfage Admin Fee",
        "WHARFAGE ADMIN FEE",
    ],
)
def test_wharfage_administration_fee_remains_distinct_from_wharfage(name):
    result = ChargeAliasResolver(load_aliases()).resolve(
        model_name=name, original_name=name, source_text=f"{name} | 1 | 5.00 | USD | 5.00"
    )

    assert result.canonical_name == "Wharfage Administration Fee"
    assert "Wharfage" not in result.matched_aliases
    assert not result.conflict
    assert not result.requires_review
    assert result.warnings == []


@pytest.mark.parametrize(
    ("original", "model", "expected"),
    [
        ("WHARFAGE", "Wharfage Administration Fee", "Wharfage"),
        ("WHARFAGE ADMINISTRATION FEE", "Wharfage", "Wharfage Administration Fee"),
    ],
)
def test_wharfage_evidence_cannot_be_reclassified_as_the_other_charge(original, model, expected):
    result = ChargeAliasResolver(load_aliases()).resolve(
        model_name=model, original_name=original, source_text=original
    )

    assert result.canonical_name == expected
    assert result.conflict
    assert result.requires_review
    assert "MODEL_CHARGE_NAME_CONFLICT" in result.warnings


@pytest.mark.parametrize("name", ["WHARFAGES", "WHARFAGEADMINISTRATIONFEE"])
def test_wharfage_rule_does_not_match_inside_another_token(name):
    result = ChargeAliasResolver(load_aliases()).resolve(
        model_name=name, original_name=name, source_text=None
    )

    assert result.canonical_name == name
    assert result.warnings == ["UNKNOWN_CHARGE_ALIAS"]
