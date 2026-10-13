"""
Minimal in-memory stand-in for the `genlayer` SDK, just enough to execute
quorum_oracle.py outside GenVM.

It lets the tests exercise the contract's real code paths (validation,
aggregation, leader/validator comparison, storage updates) with scripted web
pages and a scripted "LLM". It is NOT the real GenVM: it cannot prove the
contract deploys or that real validators agree. For that, run the contract in
GenLayer Studio (see README).
"""

import sys
import types
import typing


class Return:
    def __init__(self, calldata):
        self.calldata = calldata


class UserError(Exception):
    def __init__(self, message=""):
        super().__init__(message)
        self.message = message


class Disagree(Exception):
    """Raised by the fake VM when the validator rejects the leader."""


class TreeMap(dict):
    pass


class _State:
    """Knobs the tests turn."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pages = {}  # url -> page text (what the leader sees)
        self.validator_pages = None  # url -> page text for the validator, if different
        self.llm = naive_llm  # callable(prompt) -> dict | str
        self.in_validator = False
        self.web_calls = 0
        self.render_breaks = False  # make web.render raise, forcing the HTML fallback
        self.llm_calls = 0



def naive_llm(prompt: str):
    """Pretends to be an LLM that returns the first number on the page. It is
    deliberately gullible: it also 'obeys' injected text that contains a number."""
    import re

    page = prompt.split("<<<PAGE\n", 1)[1].rsplit("\nPAGE>>>", 1)[0]
    match = re.search(r"\d[\d,]*(?:\.\d+)?", page)
    return {"raw": match.group(0) if match else None}


state = _State()


class _Response:
    def __init__(self, text: str):
        self.body = text.encode("utf-8")


def _web_get(url):
    state.web_calls += 1
    pages = state.validator_pages if (state.in_validator and state.validator_pages is not None) else state.pages
    if url not in pages:
        raise RuntimeError("fetch failed: " + url)
    return _Response(pages[url])


def _web_render(url, mode="text"):
    if state.render_breaks:
        raise RuntimeError("render unavailable")
    state.web_calls += 1
    pages = state.validator_pages if (state.in_validator and state.validator_pages is not None) else state.pages
    if url not in pages:
        raise RuntimeError("render failed: " + url)
    return pages[url]


def _exec_prompt(prompt, response_format=None):
    state.llm_calls += 1
    return state.llm(prompt)


def _run_nondet_unsafe(leader_fn, validator_fn):
    state.in_validator = False
    result = leader_fn()
    state.in_validator = True
    try:
        accepted = validator_fn(Return(result))
    finally:
        state.in_validator = False
    if not accepted:
        raise Disagree("validator rejected the leader result")
    return result


class _Contract:
    """Initialises annotated storage fields the way GenVM would."""

    def __new__(cls, *args, **kwargs):
        self = super().__new__(cls)
        hints = {}
        for klass in reversed(cls.__mro__):
            hints.update(getattr(klass, "__annotations__", {}))
        for name, annotation in hints.items():
            origin = typing.get_origin(annotation) or annotation
            setattr(self, name, TreeMap() if origin is TreeMap else 0)
        return self


def _identity(fn):
    return fn


gl = types.SimpleNamespace(
    Contract=_Contract,
    public=types.SimpleNamespace(view=_identity, write=_identity),
    message=types.SimpleNamespace(sender_address="0xOWNER"),
    nondet=types.SimpleNamespace(
        web=types.SimpleNamespace(get=_web_get, render=_web_render),
        exec_prompt=_exec_prompt,
    ),
    vm=types.SimpleNamespace(
        Return=Return, UserError=UserError, run_nondet_unsafe=_run_nondet_unsafe
    ),
)


def install():
    module = types.ModuleType("genlayer")
    module.gl = gl
    module.u256 = int
    module.Address = str
    module.TreeMap = TreeMap
    module.allow_storage = _identity
    module.__all__ = ["gl", "u256", "Address", "TreeMap", "allow_storage"]
    sys.modules["genlayer"] = module
