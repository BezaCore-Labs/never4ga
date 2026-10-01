"""Machine-local configuration (core/05 section 9).

The vault holds canonical configuration as Markdown. Everything that describes
*this machine* -- where the vault is, which port the service answers on --
lives outside it, in TOML that Never4gA reads and never writes.

The loopback rule is tested here rather than in the service because this is
where it can be enforced once: a host that is not loopback is refused at parse
time, so no code path downstream can bind one by accident
(core/05 section 12, details/security-configuration.md section 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.config import DEFAULT_PORT, LocalConfig, ServiceEndpoint
from never4ga.errors import ConfigurationError


class TestDefaults:
    def test_an_absent_file_is_not_an_error(self, tmp_path: Path) -> None:
        # A user who never wrote a config file still gets a working service.
        config = LocalConfig.load(tmp_path / "never4ga" / "config.toml")
        assert config == LocalConfig()

    def test_the_default_endpoint_is_loopback(self) -> None:
        assert LocalConfig().service.host == "127.0.0.1"
        assert LocalConfig().service.port == DEFAULT_PORT

    def test_the_default_config_names_no_vault(self) -> None:
        # `--vault` and NEVER4GA_VAULT still work with no config file at all.
        assert LocalConfig().vault is None


class TestParsing:
    def test_reads_the_vault_path(self) -> None:
        config = LocalConfig.parse('vault = "~/notes"\n')
        assert config.vault == Path("~/notes").expanduser()

    def test_reads_the_service_endpoint(self) -> None:
        config = LocalConfig.parse('[service]\nhost = "127.0.0.2"\nport = 9999\n')
        assert config.service == ServiceEndpoint(host="127.0.0.2", port=9999)

    def test_an_unknown_key_is_preserved_rather_than_rejected(self) -> None:
        # core/02 section 31's liberality applies to local config too: a key
        # written by a later version must not stop this one from starting.
        config = LocalConfig.parse("[service]\nport = 9999\nfuture_option = true\n")
        assert config.service.port == 9999

    def test_malformed_toml_names_the_file_problem(self) -> None:
        with pytest.raises(ConfigurationError, match="config"):
            LocalConfig.parse("[service\nport = 1")

    def test_a_non_integer_port_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="port"):
            LocalConfig.parse('[service]\nport = "nine"\n')

    def test_a_port_outside_the_valid_range_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="port"):
            LocalConfig.parse("[service]\nport = 70000\n")

    def test_a_negative_port_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="port"):
            LocalConfig.parse("[service]\nport = -1\n")

    def test_port_zero_asks_the_operating_system_for_one(self) -> None:
        # Useful to a test, and to a user who does not want to pick a number.
        assert LocalConfig.parse("[service]\nport = 0\n").service.port == 0


class TestTheServiceBindsLoopbackOnly:
    """core/05 section 12: it MUST NOT bind 0.0.0.0. Refuse it at the source."""

    @pytest.mark.parametrize(
        "host",
        ["0.0.0.0", "::", "192.168.1.10", "192.0.2.10", "example.com"],
    )
    def test_a_non_loopback_host_is_refused(self, host: str) -> None:
        with pytest.raises(ConfigurationError, match="loopback"):
            LocalConfig.parse(f'[service]\nhost = "{host}"\n')

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
    def test_a_loopback_host_is_accepted(self, host: str) -> None:
        assert LocalConfig.parse(f'[service]\nhost = "{host}"\n').service.host == host

    def test_the_endpoint_refuses_a_non_loopback_host_when_constructed_directly(self) -> None:
        # The rule lives in the type, not only in the parser, so no other
        # caller can construct an endpoint that binds the network.
        with pytest.raises(ConfigurationError, match="loopback"):
            ServiceEndpoint(host="0.0.0.0")


class TestLoading:
    def test_reads_the_file_at_the_configured_path(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("[service]\nport = 8123\n")
        assert LocalConfig.load(path).service.port == 8123

    def test_a_directory_where_the_file_belongs_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.mkdir()
        with pytest.raises(ConfigurationError):
            LocalConfig.load(path)

    def test_the_default_location_is_under_the_platform_config_directory(
        self, tmp_path: Path
    ) -> None:
        from never4ga.platform_paths import PlatformPaths

        paths = PlatformPaths.resolve(environment={"XDG_CONFIG_HOME": str(tmp_path)})
        assert LocalConfig.default_path(paths) == tmp_path / "never4ga" / "config.toml"


class TestTheBaseUrl:
    def test_renders_a_loopback_url(self) -> None:
        assert ServiceEndpoint().base_url == f"http://127.0.0.1:{DEFAULT_PORT}"

    def test_brackets_an_ipv6_host(self) -> None:
        assert ServiceEndpoint(host="::1", port=8080).base_url == "http://[::1]:8080"


class TestRetiredSettings:
    """A key that no longer does anything says so.

    The `[embedding]` block configured the vector lane, which is retired, and
    nothing reads it. A block left behind still points at a live-looking host
    and can mislead anyone diagnosing a problem. Dead configuration that
    *looks* live is worse than no configuration, so Never4gA names it.

    Deliberately a known list rather than "anything unrecognised". An unknown
    key is tolerated on purpose, so a config written by a later version does
    not make this one complain; only a key Never4gA knows it has retired earns
    a word.
    """

    def test_a_retired_table_is_reported(self) -> None:
        config = LocalConfig.parse('[embedding]\nhost = "http://192.0.2.10:11434"\n')
        assert [setting.key for setting in config.retired] == ["embedding"]

    def test_the_reason_says_what_retired_it(self) -> None:
        (setting,) = LocalConfig.parse("[embedding]\nmodel = 'x'\n").retired
        assert "vector lane is retired" in setting.reason

    def test_a_config_without_one_reports_nothing(self) -> None:
        assert LocalConfig.parse('vault = "~/v"\n').retired == ()

    def test_an_unknown_key_is_not_a_retired_one(self) -> None:
        # Forward compatibility is the point: a table this version has never
        # heard of is a later version's, not a dead one.
        assert LocalConfig.parse("[something_new]\nkey = 1\n").retired == ()

    def test_a_retired_table_still_parses_the_rest(self) -> None:
        config = LocalConfig.parse('vault = "~/v"\n[embedding]\nmodel = "x"\n')
        assert config.vault is not None and config.retired
