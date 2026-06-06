import re

from logdoc.detector import CustomPattern, ErrorDetector, ErrorKind, detect, is_stack_continuation


def test_builtin_network():
    err = detect("Error: ECONNREFUSED", 1)
    assert err is not None
    assert err.kind == ErrorKind.NETWORK


def test_custom_pattern_first():
    det = ErrorDetector(
        [CustomPattern("acme", ErrorKind.BUILD, re.compile(r"ACME_FAIL", re.I))]
    )
    err = det.detect("ACME_FAIL code 9", 2)
    assert err is not None
    assert err.pattern == "acme"
    assert err.kind == ErrorKind.BUILD


def test_stack_continuation():
    assert is_stack_continuation('  File "app.py", line 1')
    assert is_stack_continuation("    at Object.<anonymous>")
    assert not is_stack_continuation("Error: totally new failure")


def test_extract_file_line():
    from logdoc.detector import extract_file_line
    lines = [
        'Traceback (most recent call last):',
        '  File "C:\\myproject\\app.py", line 12, in main',
        '    some_func()',
        '  File "C:\\myproject\\utils.py", line 45, in some_func',
        '    raise ValueError("bad")',
    ]
    res = extract_file_line(lines)
    assert res is not None
    assert res[0] == "C:\\myproject\\utils.py"
    assert res[1] == 45
