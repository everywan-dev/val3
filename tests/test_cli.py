def test_version(env):
    result = env.run("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == "val3 0.1.0"


def test_no_command_is_a_usage_error(env):
    result = env.run()
    assert result.returncode == 2
    assert "usage: val3" in result.stderr


def test_unknown_command_is_a_usage_error(env):
    assert env.run("frobnicate").returncode == 2


def test_harness_database_is_reachable(env):
    assert env.sql("SELECT current_database()") == [("val3",)]
