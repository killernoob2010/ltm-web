"""Static safety checks for the Windows portable collector build."""

from pathlib import Path


ROOT = Path(__file__).parents[1]
COLLECTOR = ROOT / "collector"
BUILD = COLLECTOR / "installer"


def test_one_click_builder_prepares_dependencies_and_emits_versioned_exe():
    script = (BUILD / "build_windows.ps1").read_text(encoding="utf-8")
    wrapper = (BUILD / "build_windows.cmd").read_text(encoding="utf-8")
    root_wrapper = (ROOT / "生成WH6安装包.cmd").read_text(encoding="utf-8")

    assert "Python.Python.3.11" in script
    assert "requirements-windows.txt" in script
    assert "WH6成交采集器.spec" in script
    assert 'WH6成交采集器-$version.exe' in script
    assert "Get-FileHash" in script
    assert "Invoke-Native" in script
    assert "Inno Setup" not in script
    assert "Setup.exe" not in script
    assert "-NoPause" in wrapper
    assert '.Extension -in @(".py", ".ps1", ".cmd", ".spec", ".txt")' in script
    assert "collector\\installer\\build_windows.cmd" in root_wrapper
    assert 'set "BUILDER=%~dp0collector\\installer\\build_windows.cmd"' in root_wrapper


def test_builder_does_not_embed_credentials_or_production_only_data():
    content = (BUILD / "build_windows.cmd").read_text(encoding="utf-8")
    assert "service_role" not in content
    assert "DATABASE_URL" not in content
    script = (BUILD / "build_windows.ps1").read_text(encoding="utf-8")
    assert "service_role" in script
    assert "DATABASE_URL" in script


def test_portable_bundle_launches_first_run_setup_and_keeps_user_data_external():
    spec = (COLLECTOR / "WH6成交采集器.spec").read_text(encoding="utf-8")
    setup_ui = (COLLECTOR / "wh6_collector" / "setup_ui.py").read_text(encoding="utf-8")
    readme = (BUILD / "README.md").read_text(encoding="utf-8")

    assert "tkinter" in spec
    assert "console=False" in spec
    assert "def run_first_setup" in setup_ui
    assert "%LOCALAPPDATA%" in readme
    assert "首次运行只选择 WH6 `Record` 目录" in readme


def test_windows_bundle_includes_iana_timezone_database_and_full_assets():
    requirements = (COLLECTOR / "requirements-windows.txt").read_text(encoding="utf-8")
    readme = (BUILD / "README.md").read_text(encoding="utf-8")
    cli = (COLLECTOR / "wh6_collector" / "cli.py").read_text(encoding="utf-8")

    assert "tzdata==2026.3" in requirements
    assert "期货和期权" in readme
    assert "position.dat" in readme
    assert "每 5 秒" in cli


def test_launcher_uses_collector_only_single_instance_mutex():
    launcher = (COLLECTOR / "launcher.py").read_text(encoding="utf-8")
    assert "Local\\LTM-WH6-Collector-B7C23B59" in launcher
    assert "CreateMutexW" in launcher
    assert "GetLastError" in launcher
    assert "WH6.exe" not in launcher
