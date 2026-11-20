"""`val3 view`: the page, its script and its stylesheet, as served."""
import re
import shutil
import subprocess

import pytest

from helpers import make_vault
from viewer import viewing

# every way a script turns text into markup or code, or sets an inline style the policy would refuse
SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "writeln", "eval(", "Function(",
         "srcdoc", "DOMParser", "createContextualFragment", "javascript:", "cssText", 'setAttribute("style"',
         "setAttribute('style'", 'setAttribute("on', "setAttribute('on", "setTimeout(\"", "setInterval(\"",
         "localStorage", "sessionStorage", "document.cookie")


@pytest.fixture
def served(env, tmp_path):
    make_vault(env)
    with viewing(env) as view:
        yield {name: view.get(name)[2].decode() for name in ("", "view.js", "view.css")}


def test_the_script_has_no_markup_sink(served):
    script = served["view.js"]
    for sink in SINKS:
        assert sink not in script, sink
    assert "textContent" in script
    assert not re.search(r"https?://", script)  # it talks to its own server only


def test_the_script_parses(served, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on the PATH")
    path = tmp_path / "view.js"
    path.write_text(served["view.js"])
    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_page_has_no_inline_code_and_no_external_request(served):
    page, style = served[""], served["view.css"]
    assert "<title>VAL3 vault view</title>" in page
    assert re.findall(r"<script[^>]*>", page) == ['<script src="view.js" defer>']
    assert "<script src=\"view.js\" defer></script>" in page
    assert "<style" not in page and " style=" not in page
    assert not re.search(r"\son[a-z]+\s*=", page)
    assert not re.search(r"https?://|//[a-z]", page + style)
    assert "@import" not in style and "url(" not in style
    assert "prefers-color-scheme: dark" in style and ':root[data-theme="dark"]' in style
