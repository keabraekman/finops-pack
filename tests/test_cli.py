from pytest import CaptureFixture

from finops_pack.cli import main


def test_main_prints_message(capsys: CaptureFixture[str]) -> None:
    main()
    captured = capsys.readouterr()
    assert "finops-pack CLI is set up." in captured.out
