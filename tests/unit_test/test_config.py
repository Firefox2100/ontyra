import pytest

from ontyra.config import Settings, load_settings


def test_defaults_when_environment_is_empty(clean_env: pytest.MonkeyPatch) -> None:
    s = load_settings(env_file=None)
    assert s.db.url == "ws://localhost:8000/rpc"
    assert (s.db.namespace, s.db.database) == ("ontyra", "main")
    assert s.log_level == "INFO"


def test_environment_variables_override_with_nested_delimiter(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("ONTYRA_DB__URL", "mem://")
    clean_env.setenv("ONTYRA_DB__DATABASE", "scratch")
    clean_env.setenv("ONTYRA_DB__TIMEOUT", "5")
    clean_env.setenv("ONTYRA_LOG_LEVEL", "DEBUG")
    s = load_settings(env_file=None)
    assert (s.db.url, s.db.database, s.db.timeout) == ("mem://", "scratch", 5.0)
    assert s.log_level == "DEBUG"


def test_password_is_secret_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ONTYRA_DB__PASSWORD", "hunter2")
    s = load_settings(env_file=None)
    assert "hunter2" not in repr(s)
    assert s.db.password.get_secret_value() == "hunter2"


def test_unrelated_variables_are_ignored(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ONTYRA_NOT_A_SETTING", "x")
    assert isinstance(load_settings(env_file=None), Settings)


def test_dotenv_file_is_read_but_environment_wins(
    clean_env: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    env_file = tmp_path / ".env"  # type: ignore[operator]
    env_file.write_text("ONTYRA_DB__DATABASE=from_file\nONTYRA_DB__NAMESPACE=from_file\n")
    clean_env.setenv("ONTYRA_DB__DATABASE", "from_env")
    s = load_settings(env_file=str(env_file))
    assert (s.db.database, s.db.namespace) == ("from_env", "from_file")


def test_policy_and_trust_come_from_the_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ONTYRA_POLICY__DEFAULT", "auto_commit")
    clean_env.setenv(
        "ONTYRA_POLICY__RULES",
        '[{"kinds": ["fact"], "actor_kinds": ["human"], "min_trust": 0.5, "decision": "auto_commit"}]',
    )
    clean_env.setenv("ONTYRA_ACTOR_TRUST__AGENT", "0.9")
    s = load_settings(env_file=None)
    assert s.policy.default == "auto_commit"
    assert s.policy.rules[0].kinds == ["fact"] and s.policy.rules[0].min_trust == 0.5
    assert s.actor_trust == {"agent": 0.9}


def test_safe_policy_default_is_review(clean_env: pytest.MonkeyPatch) -> None:
    assert load_settings(env_file=None).policy.default == "review"
