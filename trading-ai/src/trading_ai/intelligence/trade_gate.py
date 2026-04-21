"""Master AND-of-all gates."""


def should_execute_trade(context: dict) -> bool:
    checks = [
        context.get("edge_ok"),
        context.get("market_ok"),
        context.get("cooldown_ok"),
        context.get("confidence_ok"),
        context.get("system_ok"),
    ]
    return all(checks)
