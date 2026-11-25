"""Distinct NovaShop feature-flagged product recommendation, search, and payment implementations."""

from __future__ import annotations

from typing import Any

NOVASHOP_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "NV-KEY-PRO",
        "name": "NovaKey Mechanical Pro",
        "category": "peripherals",
        "tagline": "Gasket-mounted aluminum wireless 75% keyboard",
        "price": 149.0,
        "popularity": 95,
        "paired_with": ["NV-MAT-WOOL", "NV-STAND-M"],
        "semantic_tags": ["keyboard", "typing", "switches", "wireless", "workstation", "desk"],
    },
    {
        "sku": "NV-DOCK-C",
        "name": "Studio Thunderbolt 4 Dock",
        "category": "connectivity",
        "tagline": "Dual 4K60, 96W host laptop charging hub",
        "price": 89.0,
        "popularity": 88,
        "paired_with": ["NV-HUB-USBC", "NV-CAM-4K"],
        "semantic_tags": ["dock", "charging", "monitor", "display", "usb-c", "laptop", "power"],
    },
    {
        "sku": "NV-STAND-M",
        "name": "MagStand CNC Aluminum",
        "category": "ergonomics",
        "tagline": "Anodized vertical laptop & tablet riser",
        "price": 45.0,
        "popularity": 76,
        "paired_with": ["NV-KEY-PRO", "NV-DOCK-C"],
        "semantic_tags": ["stand", "ergonomic", "posture", "desk", "laptop", "riser"],
    },
    {
        "sku": "NV-CAM-4K",
        "name": "ClarityStream 4K HDR Webcam",
        "category": "video",
        "tagline": "Sony STARVIS sensor with auto framing for remote meetings",
        "price": 129.0,
        "popularity": 84,
        "paired_with": ["NV-DOCK-C", "NV-KEY-PRO"],
        "semantic_tags": ["camera", "webcam", "video", "meeting", "streaming", "conference"],
    },
    {
        "sku": "NV-HUB-USBC",
        "name": "AeroHub 10Gbps Travel Adapter",
        "category": "connectivity",
        "tagline": "Compact 6-in-1 NVMe/HDMI aluminum dongle",
        "price": 39.0,
        "popularity": 72,
        "paired_with": ["NV-DOCK-C", "NV-STAND-M"],
        "semantic_tags": ["adapter", "hdmi", "travel", "portable", "usb-c", "hub"],
    },
    {
        "sku": "NV-MAT-WOOL",
        "name": "Merino Felt Desk Mat",
        "category": "workspace",
        "tagline": "Acoustic dampening wool desk pad for mechanical keyboards",
        "price": 34.0,
        "popularity": 68,
        "paired_with": ["NV-KEY-PRO", "NV-STAND-M"],
        "semantic_tags": ["mat", "felt", "desk", "workspace", "acoustic", "pad"],
    },
]

_SYNONYM_MAP: dict[str, set[str]] = {
    "typing": {"keyboard", "switches", "wireless", "felt"},
    "keyboard": {"typing", "switches", "mat"},
    "charger": {"charging", "power", "dock", "usb-c"},
    "charging": {"power", "dock", "usb-c"},
    "monitor": {"display", "dock", "hdmi"},
    "ergonomics": {"ergonomic", "posture", "riser", "stand", "desk"},
    "call": {"webcam", "camera", "video", "meeting", "conference"},
    "office": {"workstation", "desk", "workspace", "ergonomic"},
}


def compute_recommendations(
    *,
    variation: str,
    kill_switch_enabled: bool,
    cart_skus: list[str] | None = None,
    traits: dict[str, Any] | None = None,
    limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    """Return (effective_algorithm, ranked_products) for recommendation_engine_v2."""
    if not kill_switch_enabled:
        var = "control_kill_switch_fallback"
    else:
        var = variation.strip().lower()

    cart_set = {s.strip().upper() for s in (cart_skus or []) if s.strip()}
    user_traits = traits or {}
    plan = str(user_traits.get("subscription_plan") or "").strip().lower()
    user_type = str(user_traits.get("user_type") or "").strip().lower()

    scored: list[tuple[float, dict[str, Any], str]] = []
    for item in NOVASHOP_CATALOG:
        if item["sku"] in cart_set:
            continue
        if var == "collaborative_v2":
            affinity = sum(
                35.0
                for other in NOVASHOP_CATALOG
                if other["sku"] in cart_set and item["sku"] in other["paired_with"]
            )
            score = affinity + item["popularity"] * 0.4
            reason = (
                "Frequently paired with items in your bag (collaborative_v2)"
                if affinity > 0
                else "Popular complement (collaborative_v2)"
            )
        elif var == "personalized_v2":
            trait_boost = 0.0
            if plan in {"enterprise", "pro", "beta"} and item["category"] in {"connectivity", "video"}:
                trait_boost += 40.0
            if user_type in {"internal", "qa"} and item["category"] in {"peripherals", "ergonomics"}:
                trait_boost += 30.0
            score = trait_boost + item["popularity"] * 0.5
            reason = f"Tailored for {plan or user_type or 'verified'} workspace profile (personalized_v2)"
        else:
            score = float(item["popularity"])
            reason = "Catalog popularity baseline (control)"
        scored.append((score, item, reason))

    scored.sort(key=lambda row: row[0], reverse=True)
    effective_algo = (
        var
        if var in {"collaborative_v2", "personalized_v2", "control_kill_switch_fallback"}
        else "control"
    )
    results = [
        {**item, "score": round(score, 1), "recommendation_reason": reason}
        for score, item, reason in scored[:limit]
    ]
    return effective_algo, results


def execute_product_search(
    *,
    query: str,
    variation: str,
    kill_switch_enabled: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """Return (effective_algorithm, matched_products) for smart_product_search."""
    q = query.strip().lower()
    if not kill_switch_enabled:
        var = "control_kill_switch_fallback"
    else:
        var = variation.strip().lower()

    if not q:
        return (
            var if var in {"ranked_v2", "semantic_v2", "control_kill_switch_fallback"} else "control",
            [{**item, "relevance": item["popularity"], "match_reason": "All catalog items"} for item in NOVASHOP_CATALOG],
        )

    tokens = [t for t in q.split() if t]
    matches: list[tuple[float, dict[str, Any], str]] = []

    for item in NOVASHOP_CATALOG:
        name_l = item["name"].lower()
        tag_l = item["tagline"].lower()
        cat_l = item["category"].lower()
        sku_l = item["sku"].lower()

        if var == "ranked_v2":
            score = 0.0
            for tok in tokens:
                if tok in name_l or tok in sku_l:
                    score += 30.0
                if tok in cat_l:
                    score += 20.0
                if tok in tag_l:
                    score += 15.0
            if score > 0:
                matches.append((score + item["popularity"] * 0.1, item, "Weighted relevance score (ranked_v2)"))
        elif var == "semantic_v2":
            expanded: set[str] = set(tokens)
            for tok in tokens:
                expanded |= _SYNONYM_MAP.get(tok, set())
            item_tags = set(item["semantic_tags"]) | set(name_l.split()) | {cat_l}
            overlap = expanded & item_tags
            direct = any(tok in name_l or tok in tag_l or tok in sku_l for tok in tokens)
            if overlap or direct:
                score = len(overlap) * 25.0 + (30.0 if direct else 0.0) + item["popularity"] * 0.1
                matches.append(
                    (
                        score,
                        item,
                        f"Semantic/intent match: {', '.join(sorted(overlap)[:3]) or 'direct'} (semantic_v2)",
                    )
                )
        else:
            if q in name_l or q in sku_l:
                matches.append((float(item["popularity"]), item, "Exact substring match (control)"))

    matches.sort(key=lambda r: r[0], reverse=True)
    effective_algo = (
        var if var in {"ranked_v2", "semantic_v2", "control_kill_switch_fallback"} else "control"
    )
    return effective_algo, [
        {**item, "relevance": round(score, 1), "match_reason": reason}
        for score, item, reason in matches
    ]
