from netswitch import exec as ex
from netswitch import log


def test_log_setup_writes_file(tmp_path):
    log.reset()
    logger = log.setup(str(tmp_path))
    logger.info("hello-log")
    for h in logger.handlers:
        h.flush()
    content = (tmp_path / "netswitch.log").read_text(encoding="utf-8")
    assert "hello-log" in content
    assert log.current_log_file() == str(tmp_path / "netswitch.log")
    log.reset()


def test_run_logs_command(tmp_path):
    log.reset()
    logger = log.setup(str(tmp_path))
    ex.run(["true"])                      # 写类命令 -> INFO
    for h in logger.handlers:
        h.flush()
    content = (tmp_path / "netswitch.log").read_text(encoding="utf-8")
    assert "OK: true" in content
    log.reset()


def test_log_falls_back_when_dir_not_writable(tmp_path, monkeypatch, capsys):
    """root 跑过之后仓库 logs/ 归 root，普通用户应自动回退而不是静默丢日志。"""
    log.reset()
    blocked = tmp_path / "blocked"
    blocked.write_text("I am a file, not a dir", encoding="utf-8")
    fallback = tmp_path / "fallback"
    monkeypatch.setattr(log, "DEFAULT_LOG_DIR", blocked)
    monkeypatch.setattr(log, "_fallback_dirs", lambda: [fallback])
    monkeypatch.delenv("NETSWITCH_LOG_DIR", raising=False)

    logger = log.setup()
    logger.info("hello-fallback")
    for h in logger.handlers:
        h.flush()
    assert (fallback / "netswitch.log").exists()
    assert log.current_log_file() == str(fallback / "netswitch.log")
    assert "不可写" in capsys.readouterr().err
    log.reset()


def test_log_records_user(tmp_path):
    log.reset()
    logger = log.setup(str(tmp_path))
    for h in logger.handlers:
        h.flush()
    content = (tmp_path / "netswitch.log").read_text(encoding="utf-8")
    assert "用户=" in content
    log.reset()


def test_reset_clears_log_file():
    log.reset()
    assert log.current_log_file() is None
