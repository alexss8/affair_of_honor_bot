from bot.domain.phrases import BLOCK_PHRASES, HIT_PHRASES, get_round_phrase

MIN_POOL_SIZE = 100
MAX_TEXT_LEN = 110  # без ников: карточка боя не должна раздуваться


def _all_templates() -> list[str]:
    return [t for pools in (HIT_PHRASES, BLOCK_PHRASES) for pool in pools.values() for t in pool]


def test_phrases_pools_and_formatting():
    for direction in ("top", "bot"):
        assert len(HIT_PHRASES[direction]) >= MIN_POOL_SIZE
        assert len(BLOCK_PHRASES[direction]) >= MIN_POOL_SIZE

        for template in HIT_PHRASES[direction]:
            assert template.startswith("🩸 ")
            formatted = template.format(att="Алиса", dfn="Боб")
            assert "Алиса" in formatted
            assert "Боб" in formatted

        for template in BLOCK_PHRASES[direction]:
            assert template.startswith("🛡 ")
            formatted = template.format(att="Алиса", dfn="Боб")
            assert "Алиса" in formatted
            assert "Боб" in formatted

    hit_res = get_round_phrase("top", is_blocked=False, att_name="Алиса", dfn_name="Боб")
    block_res = get_round_phrase("bot", is_blocked=True, att_name="Алиса", dfn_name="Боб")
    assert hit_res.startswith("🩸 ") and "Алиса" in hit_res and "Боб" in hit_res
    assert block_res.startswith("🛡 ") and "Алиса" in block_res and "Боб" in block_res


def test_phrases_are_unique():
    templates = _all_templates()
    duplicates = {t for t in templates if templates.count(t) > 1}
    assert not duplicates


def test_phrases_are_short():
    too_long = [t for t in _all_templates() if len(t.replace("{att}", "").replace("{dfn}", "")) > MAX_TEXT_LEN]
    assert not too_long


def test_hit_phrases_mention_lost_life():
    for pool in HIT_PHRASES.values():
        for template in pool:
            assert "-1" in template or "1 жизн" in template or "Минус" in template, template
