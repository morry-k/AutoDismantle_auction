# backend/services/calculator.py
from typing import Dict, Any, Tuple, Optional

DEFAULT_MARKET = {
    "iron_yen_per_kg": 40,
    "al_yen_per_kg": 300,
    "cu_yen_per_kg": 1200,
    "catalyst_base_yen": 15000,
}

def estimate_resource_value(
    vehicle: Dict[str, Any],
    market: Optional[Dict[str, Any]],
) -> Tuple[int, Dict[str, Any]]:
    m = dict(DEFAULT_MARKET)
    if market:
        m.update(market)

    # 超簡易な重量・比率（MVPの仮置き）
    est_weight_kg = 1200 if ("プリウス" in (vehicle.get("car_name") or "")) else 1100
    iron_ratio, al_ratio, cu_ratio = 0.75, 0.10, 0.01

    breakdown = {
        "iron_kg": int(est_weight_kg * iron_ratio),
        "aluminum_kg": int(est_weight_kg * al_ratio),
        "copper_kg": int(est_weight_kg * cu_ratio),
        "catalyst_yen": m["catalyst_base_yen"],
    }
    resource_value = int(
        breakdown["iron_kg"] * m["iron_yen_per_kg"] +
        breakdown["aluminum_kg"] * m["al_yen_per_kg"] +
        breakdown["copper_kg"] * m["cu_yen_per_kg"] +
        breakdown["catalyst_yen"]
    )
    return resource_value, breakdown

def recommend_bid(resource_value: int, reuse_bonus: int = 0, safety_ratio: float = 0.75) -> int:
    return int((resource_value + reuse_bonus) * safety_ratio)
