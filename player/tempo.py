def normalize_click_multiplier(value) -> int:
    try:
        multiplier = int(value)
    except (TypeError, ValueError):
        return 1
    return multiplier if multiplier in (1, 2) else 1


def click_bpm(tempo, multiplier=1):
    if tempo is None:
        return None
    return float(tempo) * normalize_click_multiplier(multiplier)
