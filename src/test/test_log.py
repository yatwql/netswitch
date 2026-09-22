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
