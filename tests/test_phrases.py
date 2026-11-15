from phrases import BLOCK_PHRASES, HIT_PHRASES, get_round_phrase

def test_phrases_pools_and_formatting():
    for direction in ("top", "bot"):
        assert len(HIT_PHRASES[direction]) >= 25
        assert len(BLOCK_PHRASES[direction]) >= 25

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
