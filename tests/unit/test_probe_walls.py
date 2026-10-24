"""The probe that checks the public tunnel's walls (deploy/public/probe_walls.py)."""

import importlib.util
import socket
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "deploy" / "public" / "probe_walls.py"
_spec = importlib.util.spec_from_file_location("probe_walls", PROBE)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_a_target_is_a_label_a_host_and_a_port():
    assert probe.parse_target("the Pi's tailnet address=100.64.0.7:22") == (
        "the Pi's tailnet address",
        "100.64.0.7",
        22,
    )
    for text in ["100.64.0.7:22", "label=100.64.0.7", "label=:22", "label=host:ssh"]:
        with pytest.raises(Exception):
            probe.parse_target(text)


def test_an_address_that_accepts_or_refuses_answers():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert probe.answers("127.0.0.1", port)
    # Closed now: the refusal is an answer too
    assert probe.answers("127.0.0.1", port)


def test_an_address_that_stays_silent_does_not(monkeypatch):
    def silent(address, timeout):
        raise TimeoutError

    monkeypatch.setattr(probe.socket, "create_connection", silent)
    assert not probe.answers("192.168.1.1", 80)


def _reaches(*answering):
    return lambda host, port: host in answering


def test_walls_hold_when_only_the_internet_answers(monkeypatch, capsys):
    monkeypatch.setattr(probe, "answers", _reaches("1.1.1.1"))
    code = probe.main(
        [
            "--open",
            "the internet=1.1.1.1:443",
            "the router=192.168.1.1:80",
            "a=10.0.0.1:22",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "none of the 2 addresses" in out and "reaches the internet" in out


def test_a_reachable_address_fails_by_its_label_only(monkeypatch, capsys):
    monkeypatch.setattr(probe, "answers", _reaches("1.1.1.1", "192.168.1.1"))
    code = probe.main(
        ["--open", "the internet=1.1.1.1:443", "the router=192.168.1.1:80"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "it reaches the router" in out
    assert "192.168" not in out  # a deploy's log is public


def test_silence_everywhere_is_no_proof(monkeypatch, capsys):
    """If the internet doesn't answer either, the probe itself can't connect."""
    monkeypatch.setattr(probe, "answers", _reaches())
    assert (
        probe.main(["--open", "the internet=1.1.1.1:443", "the router=192.168.1.1:80"])
        == 1
    )
    assert "does not reach the internet" in capsys.readouterr().out
