"""Guard against drift between ``WorkdirFailureKind`` and its locale copy map.

The locale dict is keyed by the enum itself; a new/renamed kind without a copy
entry must fail here instead of silently rendering the generic workdir message.
"""

from __future__ import annotations

import pytest

from locales import get_strings
from services.workdir_failures import WorkdirFailureKind

_EXPECTED_COPY_ATTR = {
    WorkdirFailureKind.OUTSIDE_ROOT: "CONFIG_INVALID_WORKDIR_OUTSIDE",
    WorkdirFailureKind.SYMLINK_ESCAPE: "CONFIG_INVALID_WORKDIR_OUTSIDE",
    WorkdirFailureKind.NOT_FOUND: "CONFIG_INVALID_WORKDIR_NOT_FOUND",
    WorkdirFailureKind.PERMISSION_DENIED: "CONFIG_INVALID_WORKDIR_PERMISSION",
    WorkdirFailureKind.FORBIDDEN: "CONFIG_INVALID_WORKDIR_FORBIDDEN",
    WorkdirFailureKind.NOT_A_DIRECTORY: "CONFIG_INVALID_WORKDIR",
    WorkdirFailureKind.INVALID_NAME: "CONFIG_INVALID_WORKDIR",
    WorkdirFailureKind.CONFLICT: "CONFIG_INVALID_WORKDIR",
    WorkdirFailureKind.DIRECTORY_NOT_EMPTY: "CONFIG_INVALID_WORKDIR",
    WorkdirFailureKind.TARGET_EXISTS: "CONFIG_INVALID_WORKDIR",
    WorkdirFailureKind.GIT_UNAVAILABLE: "CONFIG_INVALID_WORKDIR",
}


def test_every_workdir_failure_kind_has_a_copy_entry() -> None:
    copy = get_strings().CONFIG_WORKDIR_FAILURE_COPY
    missing = [kind for kind in WorkdirFailureKind if kind not in copy]
    assert missing == [], f"missing copy for workdir kinds: {missing}"


def test_copy_is_keyed_by_enum_members_not_plain_strings() -> None:
    copy = get_strings().CONFIG_WORKDIR_FAILURE_COPY
    non_enum = [key for key in copy if not isinstance(key, WorkdirFailureKind)]
    assert non_enum == [], f"copy keys must be WorkdirFailureKind members: {non_enum}"


@pytest.mark.parametrize(("kind", "copy_attr"), list(_EXPECTED_COPY_ATTR.items()))
def test_copy_for_each_kind_is_unchanged(kind: WorkdirFailureKind, copy_attr: str) -> None:
    strings = get_strings()
    assert strings.CONFIG_WORKDIR_FAILURE_COPY[kind] == getattr(strings, copy_attr)
