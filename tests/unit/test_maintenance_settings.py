"""`[maintenance]` -- the cadences §22 says must not be hardcoded.

`details/data-indexing-maintenance.md` §22 divides maintenance into three tiers
and says the exact cadence is configurable rather than hardcoded into canonical
files. Maintenance runs in-process on the service, and this table says how
often.

Absent is the ordinary case, as for the rest of the local config: a machine
with no config gets the defaults and the service starts. Unlike `[embedding]`,
absent does not mean *off*: an embedder is a machine that may not exist, while
maintenance is the service doing its own job.
"""

from __future__ import annotations

import pytest

from never4ga.config import LocalConfig, MaintenanceSettings
from never4ga.errors import ConfigurationError


def parse(text: str) -> MaintenanceSettings:
    return LocalConfig.parse(text).maintenance


class TestTheDefaults:
    def test_an_absent_table_is_the_defaults(self) -> None:
        assert parse("") == MaintenanceSettings()

    def test_and_it_is_on(self) -> None:
        # The service already reconciles the index without being asked. Doing
        # the maintenance it was told to do is the same kind of thing.
        assert parse("").enabled

    def test_the_frequent_tier_is_more_often_than_the_periodic_one(self) -> None:
        defaults = MaintenanceSettings()
        assert defaults.frequent < defaults.periodic


class TestReadingIt:
    def test_the_cadences_are_read(self) -> None:
        settings = parse("[maintenance]\nfrequent = 60.0\nperiodic = 900.0\n")
        assert (settings.frequent, settings.periodic) == (60.0, 900.0)

    def test_an_integer_is_a_number_of_seconds_too(self) -> None:
        assert parse("[maintenance]\nfrequent = 60\n").frequent == 60.0

    def test_it_can_be_switched_off(self) -> None:
        # Nothing runs when the service does not. Somebody who wants the same on
        # a running service should not have to stop the service to get it.
        assert not parse("[maintenance]\nenabled = false\n").enabled

    def test_one_cadence_can_be_set_without_the_other(self) -> None:
        settings = parse("[maintenance]\nperiodic = 7200\n")
        assert settings.periodic == 7200.0
        assert settings.frequent == MaintenanceSettings().frequent


class TestWhatIsRefused:
    @pytest.mark.parametrize("value", ["0", "-1", '"soon"', "true"])
    def test_a_cadence_must_be_a_positive_number(self, value: str) -> None:
        with pytest.raises(ConfigurationError, match="frequent"):
            parse(f"[maintenance]\nfrequent = {value}\n")

    def test_the_table_must_be_a_table(self) -> None:
        with pytest.raises(ConfigurationError, match=r"\[maintenance\]"):
            parse('maintenance = "yes"\n')

    def test_enabled_must_be_a_boolean(self) -> None:
        with pytest.raises(ConfigurationError, match="enabled"):
            parse('[maintenance]\nenabled = "yes"\n')

    def test_an_unknown_key_is_kept_rather_than_refused(self) -> None:
        # The rule the whole config follows: a config written by a later
        # version must not stop this one starting.
        assert parse("[maintenance]\nfrequent = 60\nsomething_later = 3\n").frequent == 60.0
