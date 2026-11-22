from hermes_cli.theseus_research.rule_trials import apply, register


def test_trial_is_one_turn_only(tmp_path):
    assert apply(tmp_path, "why?", "A", "1")
    register(tmp_path, "retry the order", "conditional")
    assert not apply(tmp_path, "retry the order", "A", "2")
    assert not apply(tmp_path, "retry the order", "A", "2")
    assert apply(tmp_path, "retry the order", "A", "3")
    assert apply(tmp_path, "retry the order", "B", "2")
    register(tmp_path, "what explains it?", "conditional")
    assert apply(tmp_path, "what explains it?", "A", "4")
