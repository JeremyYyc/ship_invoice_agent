import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal


def normalize_alias_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()


@dataclass(frozen=True)
class ChargeAliasResolution:
    canonical_name: str
    matched_aliases: list[str] = field(default_factory=list)
    evidence_source: str = "fallback"
    model_name: str | None = None
    model_canonical: str | None = None
    conflict: bool = False
    requires_review: bool = False
    warnings: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _AliasRule:
    canonical: str
    alias: str
    tokens: tuple[str, ...]


class ChargeAliasResolver:
    """Resolve charge aliases from original evidence before trusting model labels."""

    def __init__(
        self,
        mapping: dict[str, list[str]],
        conflict_policy: Literal["review", "override"] = "review",
    ):
        self.conflict_policy = conflict_policy
        self.exact_index: dict[str, str] = {}
        self.rules: list[_AliasRule] = []
        for canonical, aliases in mapping.items():
            if not normalize_alias_text(canonical):
                raise ValueError("charge alias canonical name cannot be empty")
            for alias in [canonical, *aliases]:
                normalized = normalize_alias_text(alias)
                if not normalized:
                    raise ValueError(f"charge alias cannot be empty for {canonical}")
                existing = self.exact_index.get(normalized)
                if existing and existing != canonical:
                    raise ValueError(
                        f"charge alias {alias!r} maps to both {existing!r} and {canonical!r}"
                    )
                self.exact_index[normalized] = canonical
                self.rules.append(
                    _AliasRule(
                        canonical=canonical,
                        alias=alias,
                        tokens=tuple(normalized.split()),
                    )
                )
        self.rules.sort(key=lambda rule: (-len(rule.tokens), -len(rule.alias), rule.alias))

    def resolve(
        self,
        *,
        model_name: str | None,
        original_name: str,
        source_text: str | None,
    ) -> ChargeAliasResolution:
        evidence_source = "original_charge_item"
        evidence_matches = self._matches(original_name)
        if not evidence_matches and source_text:
            evidence_source = "source_text"
            evidence_matches = self._matches(source_text)

        model_matches = self._matches(model_name)
        evidence_canonicals = {rule.canonical for rule in evidence_matches}
        model_canonicals = {rule.canonical for rule in model_matches}

        if len(evidence_canonicals) > 1:
            return ChargeAliasResolution(
                canonical_name=model_name or original_name,
                matched_aliases=self._unique_aliases(evidence_matches),
                evidence_source=evidence_source,
                model_name=model_name,
                requires_review=True,
                warnings=["AMBIGUOUS_CHARGE_ALIAS"],
                reasoning=[
                    "original charge evidence matched multiple canonical charge names"
                ],
            )

        if len(evidence_canonicals) == 1:
            canonical = next(iter(evidence_canonicals))
            model_canonical = (
                next(iter(model_canonicals)) if len(model_canonicals) == 1 else None
            )
            conflict = model_canonical is not None and model_canonical != canonical
            warnings: list[str] = []
            reasoning = [
                f"{evidence_source} alias mapped deterministically to {canonical}"
            ]
            if conflict:
                warnings.extend(
                    ["MODEL_CHARGE_NAME_CONFLICT", "MODEL_CHARGE_NAME_OVERRIDDEN"]
                )
                reasoning.append(
                    f"model proposed {model_canonical}; deterministic evidence took precedence"
                )
            return ChargeAliasResolution(
                canonical_name=canonical,
                matched_aliases=self._unique_aliases(evidence_matches),
                evidence_source=evidence_source,
                model_name=model_name,
                model_canonical=model_canonical,
                conflict=conflict,
                requires_review=conflict and self.conflict_policy == "review",
                warnings=warnings,
                reasoning=reasoning,
            )

        if len(model_canonicals) > 1:
            return ChargeAliasResolution(
                canonical_name=model_name or original_name,
                matched_aliases=self._unique_aliases(model_matches),
                evidence_source="model_charge_item",
                model_name=model_name,
                requires_review=True,
                warnings=["AMBIGUOUS_CHARGE_ALIAS"],
                reasoning=["model charge name matched multiple canonical charge names"],
            )

        if len(model_canonicals) == 1:
            canonical = next(iter(model_canonicals))
            return ChargeAliasResolution(
                canonical_name=canonical,
                matched_aliases=self._unique_aliases(model_matches),
                evidence_source="model_charge_item",
                model_name=model_name,
                model_canonical=canonical,
                reasoning=[f"model charge name mapped to {canonical}"],
            )

        fallback = (model_name or original_name).strip()
        return ChargeAliasResolution(
            canonical_name=fallback,
            evidence_source="fallback",
            model_name=model_name,
            warnings=["UNKNOWN_CHARGE_ALIAS"],
            reasoning=["no configured charge alias matched; preserved extracted name"],
        )

    def resolve_name(self, value: str) -> str:
        return self.resolve(
            model_name=value,
            original_name=value,
            source_text=None,
        ).canonical_name

    def _matches(self, value: str | None) -> list[_AliasRule]:
        tokens = tuple(normalize_alias_text(value).split())
        if not tokens:
            return []
        candidates: list[tuple[_AliasRule, int, int]] = []
        for rule in self.rules:
            width = len(rule.tokens)
            for index in range(len(tokens) - width + 1):
                if tokens[index : index + width] == rule.tokens:
                    candidates.append((rule, index, index + width))

        matches: list[_AliasRule] = []
        for rule, start, end in candidates:
            contained_by_longer_match = any(
                other_start <= start
                and end <= other_end
                and len(other_rule.tokens) > len(rule.tokens)
                for other_rule, other_start, other_end in candidates
            )
            if not contained_by_longer_match:
                matches.append(rule)
        return matches

    @staticmethod
    def _unique_aliases(matches: list[_AliasRule]) -> list[str]:
        return list(dict.fromkeys(rule.alias for rule in matches))
