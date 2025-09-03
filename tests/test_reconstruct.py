import json

from berghain.runner import _reconstruct_from_log


def test_reconstruct_from_log(tmp_path):
    p = tmp_path / "run.ndjson"
    events = [
        {
            "event": "start",
            "scenario": 1,
            "gameId": "g-123",
            "capacity": 1000,
            "constraints": {"A": 2},
        },
        {
            "event": "request",
            "scenario": 1,
            "send_person_index": 1,
            "decide_for_index": 0,
            "decide_for_attrs": {"A": True},
            "accept": True,
        },
        {
            "event": "response",
            "scenario": 1,
            "admitted": 1,
            "rejected": 0,
            "status": "running",
            "next_person_index": 1,
        },
    ]
    with p.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    state = _reconstruct_from_log(str(p))
    assert state["game_id"] == "g-123"
    assert state["scenario"] == 1
    assert state["capacity"] == 1000
    assert state["constraints"] == {"A": 2}
    assert state["next_index"] == 1
    assert state["prev_accept"] is True
