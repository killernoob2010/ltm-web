"""Static contract checks for the Windows one-click build bundle."""

from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "collector" / "installer"


def test_one_click_builder_prepares_dependencies_and_emits_setup_exe():
    script = (INSTALLER / "build_windows.ps1").read_text(encoding="utf-8")
    wrapper = (INSTALLER / "build_windows.cmd").read_text(encoding="utf-8")
    root_wrapper = (ROOT / "生成WH6安装包.cmd").read_text(encoding="utf-8")

    assert "Python.Python.3.11" in script
    assert "JRSoftware.InnoSetup" in script
    assert "requirements-windows.txt" in script
    assert "WH6成交采集器.spec" in script
    assert "WH6成交采集器.iss" in script
    assert "WH6成交采集器-Setup.exe" in script
    assert "Get-FileHash" in script
    assert "Invoke-Native" in script
    assert "-NoPause" in wrapper
    assert '.Extension -in @(".py", ".ps1", ".cmd", ".iss", ".spec", ".txt")' in script
    assert "collector\\installer\\build_windows.cmd" in root_wrapper
    assert 'set "BUILDER=%~dp0collector\\installer\\build_windows.cmd"' in root_wrapper
    assert 'if not exist "%BUILDER%"' in root_wrapper
    assert "未找到 WH6 构建脚本" in root_wrapper
    assert "build_windows.ps1" in wrapper


def test_builder_is_staging_only_and_does_not_embed_credentials():
    for name in ("build_windows.cmd", "WH6成交采集器.iss"):
        content = (INSTALLER / name).read_text(encoding="utf-8")
        assert "ltm-web-gt13.onrender.com" not in content
        assert "service_role" not in content
        assert "DATABASE_URL" not in content
        if name.endswith(".iss"):
            assert "OutputBaseFilename={#MyAppName}-Setup" in content

    script = (INSTALLER / "build_windows.ps1").read_text(encoding="utf-8")
    assert '"ltm-web-gt13.onrender.com"' in script
    assert "Get-FileHash" in script


def test_installer_launches_first_run_setup_and_persists_user_data_outside_program_dir():
    spec = (ROOT / "collector" / "WH6成交采集器.spec").read_text(encoding="utf-8")
    iss = (INSTALLER / "WH6成交采集器.iss").read_text(encoding="utf-8")

    assert "tkinter" in spec
    assert "def run_first_setup" in (ROOT / "collector" / "wh6_collector" / "setup_ui.py").read_text(encoding="utf-8")
    assert "{userstartup}" in iss
    assert "%LOCALAPPDATA%" in (INSTALLER / "README.md").read_text(encoding="utf-8")


def test_onefile_pyinstaller_output_is_the_file_consumed_by_inno_setup():
    script = (INSTALLER / "build_windows.ps1").read_text(encoding="utf-8")
    iss = (INSTALLER / "WH6成交采集器.iss").read_text(encoding="utf-8")

    assert 'dist\\WH6成交采集器.exe' in script
    assert 'Source: "{#BuildDir}\\WH6成交采集器.exe"' in iss


def test_windows_bundle_includes_iana_timezone_database():
    requirements = (ROOT / "collector" / "requirements-windows.txt").read_text(encoding="utf-8")

    assert "tzdata==2026.3" in requirements
