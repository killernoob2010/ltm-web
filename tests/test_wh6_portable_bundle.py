"""Static contract checks for the single-file Windows collector bundle."""

from pathlib import Path


ROOT = Path(__file__).parents[1]
COLLECTOR = ROOT / "collector"


def test_windows_build_chain_emits_one_portable_exe_without_setup_dependency():
    script = (COLLECTOR / "installer" / "build_windows.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-wh6-windows.yml").read_text(encoding="utf-8")
    assert "PyInstaller" in script
    assert 'WH6成交采集器-$version.exe' in script
    assert "Copy-Item" in script
    assert "Get-FileHash" in script
    assert "Inno Setup" not in script
    assert "WH6成交采集器.iss" not in script
    assert "Setup.exe" not in script
    assert "Inno Setup" not in workflow
    assert "Setup.exe" not in workflow
    assert "WH6成交采集器-0.3.0.exe" in workflow


def test_pyinstaller_spec_is_windowed_single_file_entrypoint():
    spec = (COLLECTOR / "WH6成交采集器.spec").read_text(encoding="utf-8")
    assert "launcher.py" in spec
    assert "console=False" in spec
    assert "COLLECT(" not in spec


def test_portable_bundle_keeps_runtime_data_outside_program_directory():
    setup_ui = (COLLECTOR / "wh6_collector" / "setup_ui.py").read_text(encoding="utf-8")
    cli = (COLLECTOR / "wh6_collector" / "cli.py").read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%" in (COLLECTOR / "installer" / "README.md").read_text(encoding="utf-8")
    assert "default_data_dir" in cli
    assert "data_dir=str(config_path.parent)" in setup_ui
