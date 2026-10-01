# -*- coding: utf-8 -*-
"""Legacy wardrobe compatibility facade.

No semantic inference is allowed here. AI-provided canonical wardrobe is preserved
verbatim; legacy clothing text is only structurally migrated, never reinterpreted.
"""
from __future__ import annotations

from copy import deepcopy

from snapgen_character_visual_bible import canonicalize_character, has_value


def normalize_character_wardrobe(entity, story=None):
    char = canonicalize_character(entity if isinstance(entity, dict) else {}, "human")
    return deepcopy(char.get("wardrobe")) if char else None


def apply_character_wardrobe(entity, story=None):
    """Compatibility no-op: never invent or mutate semantic wardrobe content."""
    return entity


def wardrobe_prompt_text(entity, story=None):
    char = canonicalize_character(entity if isinstance(entity, dict) else {}, "human")
    wardrobe = char.get("wardrobe") if char else None
    if not isinstance(wardrobe, dict):
        return "none; do not invent human clothing"
    labels = (
        ("top", "top"), ("bottom", "bottom"), ("footwear", "footwear"),
        ("outerwear", "outerwear"), ("accessories", "accessories"),
        ("colors", "colors"), ("materials", "materials"), ("condition", "condition"),
        ("overall_style", "overall_style"), ("source", "source"), ("reason", "reason"),
    )
    return "; ".join(f"{label}: {wardrobe.get(key)}" for key, label in labels if has_value(wardrobe.get(key))) or "none"
