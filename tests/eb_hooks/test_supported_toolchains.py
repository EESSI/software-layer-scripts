# Unit tests for the supported top-level toolchains (eessi_supported_toolchains.toml)
# and the function in eb_hooks.py that loads them.
# Requires EasyBuild to be importable, e.g.: pip install easybuild pytest
import os
import sys

import pytest

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, REPO_ROOT)

import eb_hooks  # noqa: E402
from easybuild.tools.build_log import EasyBuildError  # noqa: E402

TOOLCHAINS_FILE = os.path.join(REPO_ROOT, 'eessi_supported_toolchains.toml')
ENVVAR = 'EESSI_SUPPORTED_TOOLCHAINS_FILE'


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(ENVVAR, raising=False)


def write_file(path, content):
    path.write_text(content)
    return str(path)


def test_toml_format():
    """Check that the shipped TOML file has the expected structure."""
    with open(TOOLCHAINS_FILE, 'rb') as fh:
        data = tomllib.load(fh)

    assert isinstance(data, dict) and data
    for eessi_version, toolchains in data.items():
        assert isinstance(eessi_version, str)
        assert isinstance(toolchains, list) and toolchains, f"No toolchains for {eessi_version}"
        for tc in toolchains:
            assert set(tc) <= {'name', 'version', 'min_easybuild_version'}, tc
            assert isinstance(tc.get('name'), str) and tc['name'], tc
            assert isinstance(tc.get('version'), str) and tc['version'], tc
            if 'min_easybuild_version' in tc:
                assert isinstance(tc['min_easybuild_version'], str), tc
        # no duplicates
        pairs = [(tc['name'], tc['version']) for tc in toolchains]
        assert len(pairs) == len(set(pairs)), f"Duplicate toolchains for {eessi_version}"


DEFAULT_FILENAME = 'eessi_supported_toolchains.toml'


@pytest.fixture
def default_dir(monkeypatch, tmp_path):
    """Make the 'directory of eb_hooks.py' (i.e. the default location of the TOML file) an empty temporary dir."""
    monkeypatch.setattr(eb_hooks, '__file__', str(tmp_path / 'eb_hooks.py'))
    return tmp_path


def test_load_default_location(default_dir):
    """Without the environment variable, the file next to eb_hooks.py is used."""
    write_file(default_dir / DEFAULT_FILENAME, '''
        "2099.01" = [
            # comments are the reason for using TOML
            { name = "foo", version = "1" },
            { name = "bar", version = "2" },
        ]
        "2099.02" = [{ name = "baz", version = "3" }]
    ''')
    assert eb_hooks.load_supported_top_level_toolchains() == {
        '2099.01': [{'name': 'foo', 'version': '1'}, {'name': 'bar', 'version': '2'}],
        '2099.02': [{'name': 'baz', 'version': '3'}],
    }


@pytest.mark.parametrize('eb_version, expected', [
    ('4.9.0', ['always']),
    ('5.2.0', ['always', 'since_5_2_0']),
    ('5.2.1', ['always', 'since_5_2_0']),
    ('5.3.0', ['always', 'since_5_2_0']),
    ('5.3.1', ['always', 'since_5_2_0', 'since_5_3_1']),
    ('6.0.0', ['always', 'since_5_2_0', 'since_5_3_1']),
])
def test_min_easybuild_version(monkeypatch, default_dir, eb_version, expected):
    """Toolchains with a 'min_easybuild_version' are only included for that EasyBuild version or newer."""
    write_file(default_dir / DEFAULT_FILENAME, '''
        "2099.01" = [
            { name = "always", version = "1" },
            { name = "since_5_2_0", version = "1", min_easybuild_version = "5.2.0" },
            { name = "since_5_3_1", version = "1", min_easybuild_version = "5.3.1" },
        ]
        "2099.02" = [
            { name = "only_future", version = "1", min_easybuild_version = "99.0.0" },
        ]
    ''')
    monkeypatch.setattr(eb_hooks, 'EASYBUILD_VERSION', eb_version)
    result = eb_hooks.load_supported_top_level_toolchains()
    assert [tc['name'] for tc in result['2099.01']] == expected
    # An EESSI version for which no toolchain is supported by this EasyBuild version is kept, with an empty list
    assert result['2099.02'] == []
    # The minimum version is not part of the returned toolchain dicts
    assert all(set(tc) == {'name', 'version'} for tcs in result.values() for tc in tcs)


def test_envvar_overrides_location(monkeypatch, default_dir, tmp_path):
    write_file(default_dir / DEFAULT_FILENAME, '"2099.01" = [{ name = "default", version = "1" }]')
    custom = write_file(tmp_path / 'custom.toml', '"2099.01" = [{ name = "custom", version = "1" }]')
    monkeypatch.setenv(ENVVAR, custom)
    assert eb_hooks.load_supported_top_level_toolchains() == {'2099.01': [{'name': 'custom', 'version': '1'}]}


def test_envvar_missing_file(monkeypatch, default_dir, tmp_path):
    default_file = write_file(default_dir / DEFAULT_FILENAME, '"2099.01" = []')
    missing = str(tmp_path / 'does_not_exist.toml')
    monkeypatch.setenv(ENVVAR, missing)
    with pytest.raises(EasyBuildError) as excinfo:
        eb_hooks.load_supported_top_level_toolchains()
    msg = str(excinfo.value)
    assert missing in msg
    assert ENVVAR in msg
    # a file exists in the default location, so the user should be pointed to it and told how to use it
    assert default_file in msg
    assert f"unset {ENVVAR}" in msg


def test_envvar_missing_file_no_default(monkeypatch, default_dir, tmp_path):
    missing = str(tmp_path / 'does_not_exist.toml')
    monkeypatch.setenv(ENVVAR, missing)
    with pytest.raises(EasyBuildError) as excinfo:
        eb_hooks.load_supported_top_level_toolchains()
    msg = str(excinfo.value)
    assert missing in msg
    assert 'unset' not in msg


def test_missing_default_file(default_dir):
    with pytest.raises(EasyBuildError) as excinfo:
        eb_hooks.load_supported_top_level_toolchains()
    msg = str(excinfo.value)
    assert str(default_dir / DEFAULT_FILENAME) in msg
    assert ENVVAR in msg  # mentions how to configure the location
    assert 'unset' not in msg


@pytest.mark.parametrize('use_envvar', [False, True])
def test_invalid_toml(monkeypatch, default_dir, tmp_path, use_envvar):
    if use_envvar:
        bad = write_file(tmp_path / 'bad.toml', '"2099.01" = [')
        monkeypatch.setenv(ENVVAR, bad)
    else:
        bad = write_file(default_dir / DEFAULT_FILENAME, '"2099.01" = [')
    with pytest.raises(EasyBuildError, match='does not contain valid TOML') as excinfo:
        eb_hooks.load_supported_top_level_toolchains()
    assert bad in str(excinfo.value)


def test_no_toml_support(monkeypatch, default_dir):
    """A clear error is raised if there is no TOML parser available (Python < 3.11 without tomli)."""
    write_file(default_dir / DEFAULT_FILENAME, '"2099.01" = []')
    monkeypatch.setattr(eb_hooks, 'tomllib', None)
    with pytest.raises(EasyBuildError, match='tomli'):
        eb_hooks.load_supported_top_level_toolchains()
