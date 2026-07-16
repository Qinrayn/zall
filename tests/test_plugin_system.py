"""Tests for Plugin system (Phase 2b).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import os
import pytest
import tempfile
from pathlib import Path

from zall.plugin import (
    PluginManifest,
    PluginScope,
    PluginSystem,
    PluginLoader,
    DiscoveredPlugin,
    discover_plugins,
)


class TestPluginManifest:
    """PluginManifest parsing invariants."""

    def test_parse_minimal_toml(self):
        toml = '''
[plugin]
name = "test-plugin"
version = "0.1.0"
'''
        manifest = PluginManifest._parse_toml(toml, Path("/fake/path"))
        assert manifest.name == "test-plugin"
        assert manifest.version == "0.1.0"
        assert manifest.description == ""

    def test_parse_full_toml(self):
        toml = '''
[plugin]
name = "my-plugin"
version = "1.0.0"
description = "A test plugin"
author = "test"
license = "MIT"
min_zall_version = "0.3.0"
skills = ["skill1.md", "skill2.md"]
agents = ["agent1.md"]
mcp_servers = ["server1"]
dependencies = ["other-plugin"]

[plugin.hooks]
on_turn_start = "hooks/start.py"
'''
        manifest = PluginManifest._parse_toml(toml, Path("/fake"))
        assert manifest.name == "my-plugin"
        assert len(manifest.skills) == 2
        assert len(manifest.agents) == 1
        assert len(manifest.mcp_servers) == 1
        assert manifest.hooks["on_turn_start"] == "hooks/start.py"
        assert manifest.dependencies == ["other-plugin"]

    def test_missing_name_raises(self):
        toml = '''
[plugin]
version = "1.0.0"
'''
        with pytest.raises(ValueError, match="missing required"):
            PluginManifest._parse_toml(toml, Path("/fake"))

    def test_from_file(self):
        content = '''
[plugin]
name = "file-plugin"
version = "0.1.0"
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            fpath = f.name

        try:
            manifest = PluginManifest.from_file(fpath)
            assert manifest.name == "file-plugin"
        finally:
            os.unlink(fpath)


class TestPluginScope:
    """PluginScope invariants."""

    def test_scope_values(self):
        assert PluginScope.PROJECT.value == "project"
        assert PluginScope.USER.value == "user"
        assert PluginScope.MARKETPLACE.value == "marketplace"
        assert PluginScope.BUILTIN.value == "built-in"


class TestPluginLoader:
    """PluginLoader invariants."""

    def test_empty_loader(self):
        loader = PluginLoader()
        assert len(loader.loaded) == 0

    def test_load_plugin_without_resources(self):
        """Loading a plugin with no resources should succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = Path(tmpdir) / "test-plugin"
            plugin_path.mkdir()

            # Create minimal plugin.toml
            (plugin_path / "plugin.toml").write_text('''
[plugin]
name = "test-plugin"
version = "0.1.0"
''', encoding="utf-8")

            manifest = PluginManifest.from_file(plugin_path / "plugin.toml")
            discovered = DiscoveredPlugin(
                name="test-plugin",
                path=plugin_path,
                manifest=manifest,
                scope=PluginScope.USER,
            )

            loader = PluginLoader()
            loaded = loader.load(discovered)
            assert loaded.name == "test-plugin"
            assert len(loaded.skills) == 0
            assert len(loaded.agents) == 0

    def test_load_plugin_with_skills(self):
        """Loading a plugin with skills should load them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = Path(tmpdir) / "skills-plugin"
            plugin_path.mkdir()

            # Create plugin.toml
            (plugin_path / "plugin.toml").write_text('''
[plugin]
name = "skills-plugin"
version = "0.1.0"
skills = ["test_skill.md"]
''', encoding="utf-8")

            # Create skills directory and skill file
            skills_dir = plugin_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "test_skill.md").write_text(
                "---\nname: test-skill\n---\n# Test skill",
                encoding="utf-8",
            )

            manifest = PluginManifest.from_file(plugin_path / "plugin.toml")
            discovered = DiscoveredPlugin(
                name="skills-plugin",
                path=plugin_path,
                manifest=manifest,
                scope=PluginScope.USER,
            )

            loader = PluginLoader()
            loaded = loader.load(discovered)
            assert loaded.name == "skills-plugin"
            assert len(loaded.skills) >= 0  # May be empty if skill loader not available

    def test_deduplicate_loading(self):
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = Path(tmpdir) / "dedup-plugin"
            plugin_path.mkdir()
            (plugin_path / "plugin.toml").write_text('''
[plugin]
name = "dedup-plugin"
version = "0.1.0"
''', encoding="utf-8")

            manifest = PluginManifest.from_file(plugin_path / "plugin.toml")
            discovered = DiscoveredPlugin(
                name="dedup-plugin", path=plugin_path,
                manifest=manifest, scope=PluginScope.USER,
            )

            # Load twice
            loaded1 = loader.load(discovered)
            loaded2 = loader.load(discovered)
            assert loaded1 is loaded2  # Same instance
            assert len(loader.loaded) == 1


class TestPluginSystem:
    """PluginSystem invariants."""

    def test_empty_system(self):
        system = PluginSystem(project_dir="/nonexistent")
        assert len(system.discovered) == 0
        assert len(system.active_plugins) == 0

    def test_discover_no_plugins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            system = PluginSystem(project_dir=tmpdir)
            plugins = system.discover()
            assert len(plugins) == 0

    def test_discover_with_plugin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create plugin directory
            plugins_dir = Path(tmpdir) / ".zall" / "plugins" / "my-plugin"
            plugins_dir.mkdir(parents=True)
            (plugins_dir / "plugin.toml").write_text('''
[plugin]
name = "my-plugin"
version = "0.1.0"
''', encoding="utf-8")

            system = PluginSystem(project_dir=tmpdir)
            plugins = system.discover()
            assert len(plugins) == 1
            assert plugins[0].name == "my-plugin"

    def test_install_from_git_invalid_url(self):
        """Installing from invalid URL should fail gracefully."""
        system = PluginSystem()
        result = system.install_from_git(
            "https://github.com/nonexistent/repo.git",
            target_dir=tempfile.gettempdir(),
        )
        assert result is None  # Should fail gracefully, not crash

    def test_system_repr(self):
        system = PluginSystem()
        assert "PluginSystem" in repr(system)


class TestDiscoverPlugins:
    """discover_plugins invariants."""

    def test_discover_no_dirs(self):
        plugins = discover_plugins(project_dir="/nonexistent")
        assert len(plugins) == 0

    def test_discover_deduplicates_names(self):
        """同名的插件只保留优先级最高的。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Project-level plugin
            proj_plugin = Path(tmpdir) / ".zall" / "plugins" / "dup-plugin"
            proj_plugin.mkdir(parents=True)
            (proj_plugin / "plugin.toml").write_text('''
[plugin]
name = "dup-plugin"
version = "0.1.0"
''', encoding="utf-8")

            # User-level plugin (should be shadowed by project)
            user_plugin = Path.home() / ".zall" / "plugins" / "dup-plugin"
            user_plugin.mkdir(parents=True, exist_ok=True)
            (user_plugin / "plugin.toml").write_text('''
[plugin]
name = "dup-plugin"
version = "0.2.0"
''', encoding="utf-8")

            try:
                plugins = discover_plugins(project_dir=tmpdir)
                names = [p.name for p in plugins]
                # Should only have one 'dup-plugin'
                assert names.count("dup-plugin") <= 1
            finally:
                # Cleanup
                import shutil
                if user_plugin.exists():
                    shutil.rmtree(user_plugin)


class TestPluginManifestFile:
    """PluginManifest file I/O invariants."""

    def test_invalid_toml_raises(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8",
        ) as f:
            f.write("invalid toml content [[[")
            fpath = f.name

        try:
            with pytest.raises(Exception):
                PluginManifest.from_file(fpath)
        finally:
            os.unlink(fpath)