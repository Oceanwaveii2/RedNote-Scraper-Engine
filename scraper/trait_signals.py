"""Transparent keyword-to-trait candidate mapping; never authors story prose."""

from __future__ import annotations

import re


RULESET = "reel-harvest.trait-signals/1"

# This small bilingual lexicon maps recurring public-facing topic signals to
# candidate trait axes. It does not decide that any trait is true or apply it.
RULES = (
    {
        "traitId": "candidate:tissue_affinity",
        "family": "prop_affinity",
        "axis": "tissue_helpfulness",
        "terms": ("tissue", "paper", "wipe", "纸巾", "卫生纸", "纸盒", "抽纸"),
    },
    {
        "traitId": "candidate:plush_texture_affinity",
        "family": "texture_affinity",
        "axis": "plush_material_attention",
        "terms": ("plush", "soft", "stuffed", "pillow", "毛绒", "柔软", "玩偶", "抱枕"),
    },
    {
        "traitId": "candidate:adaptive_mobility",
        "family": "body_awareness",
        "axis": "movement_adaptation",
        "terms": ("walk", "crawl", "balance", "mobility", "走路", "爬", "平衡", "行动"),
    },
    {
        "traitId": "candidate:style_experimentation",
        "family": "self_styling",
        "axis": "presentation_drive",
        "terms": ("makeup", "lip", "gloss", "beauty", "fashion", "化妆", "口红", "唇", "美妆", "妆容"),
    },
    {
        "traitId": "candidate:comic_display",
        "family": "comic_expression",
        "axis": "reaction_timing",
        "terms": ("funny", "laugh", "absurd", "meme", "reaction", "搞笑", "好笑", "笑死", "表情包", "抽象"),
    },
    {
        "traitId": "candidate:social_banter",
        "family": "social_style",
        "axis": "teasing_and_repair",
        "terms": ("tease", "mock", "embarrass", "banter", "调侃", "吐槽", "嘲笑", "尴尬"),
    },
    {
        "traitId": "candidate:silhouette_awareness",
        "family": "presentation_awareness",
        "axis": "silhouette_attention",
        "terms": ("shape", "silhouette", "display", "big butt", "booty", "身材", "造型", "臀", "展示"),
    },
    {
        "traitId": "candidate:continuity_resistance",
        "family": "identity_continuity",
        "axis": "change_acceptance_or_reversal",
        "terms": ("revert", "restore", "change back", "normal again", "恢复", "变回", "还原", "原样"),
    },
)


def _has_term(text: str, term: str) -> bool:
    folded_text, folded_term = text.casefold(), term.casefold()
    if all(ord(char) < 128 for char in term):
        return re.search(r"(?<![\w])" + re.escape(folded_term) + r"(?![\w])", folded_text) is not None
    return folded_term in folded_text


def derive_trait_candidates(metadata: dict, comments: dict) -> dict:
    post_id = str(metadata.get("postId") or "")
    documents: list[tuple[str, str]] = []
    if metadata.get("title"):
        documents.append(("title", str(metadata["title"])))
    if metadata.get("text"):
        documents.append(("caption", str(metadata["text"])))
    for index, comment in enumerate(comments.get("comments", []), start=1):
        if isinstance(comment, dict) and isinstance(comment.get("text"), str):
            documents.append((f"comment:{index}", comment["text"]))

    candidates = []
    for rule in RULES:
        evidence = []
        matched_terms: set[str] = set()
        for source_field, text in documents:
            matches = [term for term in rule["terms"] if _has_term(text, term)]
            if matches:
                matched_terms.update(matches)
                evidence.append({"postId": post_id, "field": source_field})
        if not evidence:
            continue
        candidates.append({
            "candidateId": f"{rule['traitId']}:{post_id}",
            "traitId": rule["traitId"],
            "family": rule["family"],
            "axis": rule["axis"],
            "evidenceCount": len(evidence),
            "sourceRefs": evidence,
            "matchedTerms": sorted(matched_terms),
            "reviewRequired": True,
            "autoApplied": False,
        })

    return {
        "schema": "noir.character-trait-candidates/1",
        "method": RULESET,
        "targetCharacter": "Rainee",
        "postId": post_id,
        "status": "pending-human-review",
        "autoApplied": False,
        "candidateCount": len(candidates),
        "candidates": candidates,
    }
