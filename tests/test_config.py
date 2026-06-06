from logdoc.config import LogDocConfig


def test_from_dict_custom_pattern():
    data = {
        "ollama": {"url": "http://localhost:11434", "model": "mistral", "default_ai": True},
        "watch": {"debounce_seconds": 1.0},
        "patterns": [{"name": "x", "kind": "database", "regex": "DB_DOWN"}],
    }
    cfg = LogDocConfig.from_dict(data)
    assert cfg.watch.use_ollama is True
    assert cfg.watch.model == "mistral"
    assert cfg.watch.debounce_seconds == 1.0
    assert len(cfg.watch.custom_patterns) == 1
    assert cfg.watch.custom_patterns[0].name == "x"


def test_merge_cli_overrides():
    cfg = LogDocConfig.from_dict({})
    merged = cfg.merge_cli(ai=True, debounce=0.3)
    assert merged.use_ollama is True
    assert merged.debounce_seconds == 0.3
