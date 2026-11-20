"""`[extractor] focus = true` adds the restriction to the extractor's prompt; without it the prompt is unchanged."""
from helpers import make_vault
from test_capture_resume import april, write

MARK = "Restriction for this project"


def system_prompt(env, n=0):
    argv = env.calls()[n]["argv"]
    return argv[argv.index("--system-prompt") + 1]


def test_the_prompt_is_unchanged_without_the_switch(env):
    make_vault(env)
    env.run("extract", str(write(env, "first", april("first"))))
    assert MARK not in system_prompt(env)


def test_focus_adds_the_restriction(env):
    make_vault(env)
    with (env.project / "val3.toml").open("a") as handle:
        handle.write("[extractor]\nfocus = true\n")
    result = env.run("extract", str(write(env, "first", april("first"))))
    assert result.returncode == 0, result.stderr
    prompt = system_prompt(env)
    assert MARK in prompt and "pitfalls" in prompt and prompt.startswith("VAL3 extractor.")


def test_focus_must_be_true_or_false(env):
    make_vault(env)
    with (env.project / "val3.toml").open("a") as handle:
        handle.write('[extractor]\nfocus = "yes"\n')
    result = env.run("status")
    assert result.returncode == 1 and "[extractor] focus must be true or false" in result.stderr
