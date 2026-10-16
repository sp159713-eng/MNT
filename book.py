def compute(capital: float) -> dict:
    import config
    import costs as costs_module
    import features as features_module
    import production

    import time

    import model_timings

    started = time.perf_counter()
    signal, bundle = production.load()
    panel = features_module.cross_sectionalize(
        features_module.build_panel(), require_target=False)
    latest = production.scored(panel)
    try:
        import explain as explain_module

        latest["why"] = explain_module.why(signal, latest)
    except Exception:
        latest["why"] = ""

    model_timings.record(bundle.get("name"), "pick",
                         time.perf_counter() - started)
    each = capital / config.TOP_K
    return {
        "rows": latest.head(config.TOP_K),
        "date": latest["timestamp"].iloc[0],
        "model": bundle["name"],
        "each": each,
        "cost": costs_module.cost_bp(each),
        "breakeven": costs_module.breakeven_move_pct(each),
        "k": config.TOP_K,
    }
