import sqlite3
import stat


def test_enrollment_private_and_non_overwriting(tmp_path, capsys):
    from scripts.enroll_totp import main
    database = tmp_path / 'pantry.db'
    sqlite3.connect(database).close()
    target = tmp_path / 'enroll'
    args = ['--slot','1','--username','alice','--db',str(database),'--output-dir',str(target)]
    assert main(args) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    for child in target.iterdir():
        assert stat.S_IMODE(child.stat().st_mode) == 0o600
    secret = (target/'totp.env').read_text().strip().split('=')[1]
    assert secret not in capsys.readouterr().out
    codes = (target/'recovery-codes.txt').read_text().splitlines()
    assert len(codes) == 10 and len(set(codes)) == 10
    conn = sqlite3.connect(database)
    assert conn.execute('SELECT count(*) FROM mfa_recovery').fetchone()[0] == 10
    assert secret.encode() not in database.read_bytes()
    assert main(args) == 1


def test_enrollment_requires_explicit_rotation(tmp_path):
    from scripts.enroll_totp import main
    database = tmp_path/'pantry.db'
    sqlite3.connect(database).close()
    common = ['--slot','1','--username','alice','--db',str(database)]
    assert main(common+['--output-dir',str(tmp_path/'one')]) == 0
    assert main(common+['--output-dir',str(tmp_path/'two')], confirm=lambda _: 'no') == 1
    assert not (tmp_path/'two').exists()
    assert main(common+['--output-dir',str(tmp_path/'three')], confirm=lambda _: 'REPLACE') == 0


def test_enrollment_refuses_git_and_missing_database(tmp_path):
    from scripts.enroll_totp import main
    database=tmp_path/'pantry.db'
    common=['--slot','1','--username','alice','--db',str(database)]
    assert main(common+['--output-dir',str(tmp_path/'one')]) == 1
    sqlite3.connect(database).close()
    (tmp_path/'.git').mkdir()
    assert main(common+['--output-dir',str(tmp_path/'one')]) == 1
