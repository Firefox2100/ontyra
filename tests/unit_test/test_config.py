import pytest
from pydantic import SecretStr

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


def test_a_blank_token_means_none_and_a_short_one_is_refused() -> None:
    from ontyra.config import AuthSettings

    assert AuthSettings(token="").token is None  # type: ignore[arg-type]  # an empty compose variable
    with pytest.raises(ValueError, match="at least 32"):
        AuthSettings(token="short")  # type: ignore[arg-type]


def test_local_deployment_allows_open_plain_http_on_loopback() -> None:
    settings = Settings.model_validate({})
    assert settings.server.deployment == "local"
    assert not settings.ui_cookie_secure


def test_open_non_loopback_needs_an_explicit_local_acknowledgement() -> None:
    with pytest.raises(ValueError, match="ALLOW_OPEN_NON_LOOPBACK"):
        Settings.model_validate({"server": {"host": "0.0.0.0"}})
    with pytest.raises(ValueError, match="ALLOW_OPEN_NON_LOOPBACK"):
        Settings.model_validate({}).for_bind("0.0.0.0", 8080)
    settings = Settings.model_validate(
        {"server": {"host": "0.0.0.0", "allow_open_non_loopback": True}}
    )
    assert settings.server.allow_open_non_loopback


def test_worker_is_open_on_loopback_but_non_loopback_requires_a_strong_token() -> None:
    settings = Settings.model_validate({})
    assert settings.worker.host == "127.0.0.1" and settings.worker.token is None
    with pytest.raises(ValueError, match="WORKER__TOKEN"):
        Settings.model_validate({"worker": {"host": "0.0.0.0"}})
    with pytest.raises(ValueError, match="WORKER__TOKEN"):
        settings.for_worker_bind("0.0.0.0", 8788)
    with pytest.raises(ValueError, match="at least 32"):
        Settings.model_validate({"worker": {"token": "short"}})
    protected = Settings.model_validate({"worker": {"host": "0.0.0.0", "token": "w" * 32}})
    assert protected.worker.token is not None


def test_public_deployment_requires_multi_user_auth_and_hardens_the_ui() -> None:
    with pytest.raises(ValueError, match="multi-user"):
        Settings.model_validate({"server": {"deployment": "public"}})
    token = SecretStr("a" * 32)
    with pytest.raises(ValueError, match="UI_ALLOWED_ORIGINS"):
        Settings.model_validate(
            {
                "server": {"deployment": "public"},
                "auth": {"multi_user": True, "admin_token": token},
                "ui": {"enabled": True},
            }
        )
    settings = Settings.model_validate(
        {
            "server": {"deployment": "public"},
            "auth": {
                "multi_user": True,
                "admin_token": token,
                "ui_allowed_origins": ["https://memory.example.com"],
            },
            "ui": {"enabled": True},
        }
    )
    assert settings.ui_cookie_secure
