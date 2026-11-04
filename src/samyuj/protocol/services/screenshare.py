from __future__ import annotations

import json
from typing import Any

from samyuj.protocol.constants import (
    PC_SS_VERSION,
    SS_CMD_PC_ANSWER,
    SS_CMD_PC_CANDIDATE,
    SS_CMD_PC_CONNECTION_READY,
    SS_CMD_PC_RECONNECT,
    SS_CMD_PC_SET_AUDIO,
    SS_CMD_PC_SET_RESOLUTION,
    SS_CMD_PC_SET_SCALE,
    SS_CMD_PC_STATS,
    SS_CMD_PC_STOP,
    SS_CMD_PC_SWITCH,
    SS_CMD_PC_VERSION,
)
from samyuj.protocol.envelope import pack_envelope

def _ss_env(
    ctx: Any,
    cmd: int,
    payload: dict[str, Any] | str,
    session_id: int | None = None,
    msg_id: int | None = None,
) -> str:
    inner = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )
    sid = session_id if session_id is not None else (getattr(ctx, "ss_session_id", 0) or 0)
    data = {"ver": "1", "cmd": int(cmd), "data": inner, "sessionId": int(sid)}
    return pack_envelope(ctx, msg_id, None, 202, 202, data)

def build_ss_connection_ready(
    ctx: Any,
    session_id: int,
    share_type: str = "extend",
    set_audio: bool = False,
    set_scale: int = 200,
    msg_id: int | None = None,
) -> str:
    return _ss_env(
        ctx,
        SS_CMD_PC_CONNECTION_READY,
        {
            "shareType": share_type or "extend",
            "setAudio": bool(set_audio),
            "setScale": int(set_scale),
            "sessionId": int(session_id),
            "key_pc_ss_version": PC_SS_VERSION,
        },
        session_id,
        msg_id,
    )

def build_ss_answer(
    ctx: Any,
    session_id: int,
    answer_sdp: str,
    msg_type: str = "answer",
    msg_id: int | None = None,
) -> str:
    return _ss_env(
        ctx,
        SS_CMD_PC_ANSWER,
        {"answer": answer_sdp, "messageType": msg_type},
        session_id,
        msg_id,
    )

def build_ss_candidate(
    ctx: Any,
    session_id: int,
    cand_json: str,
    msg_type: str = "candidate",
    msg_id: int | None = None,
    sdp_mid: str = "0",
    sdp_mline_index: int = 0,
) -> str:
    s = str(cand_json or "").strip()
    if not s.startswith("{"):
        if s and not s.startswith("candidate:"):
            s = "candidate:" + s
        s = json.dumps(
            {
                "label": int(sdp_mline_index),
                "id": str(sdp_mid),
                "candidate": s,
                "type": msg_type,
            },
            separators=(",", ":"),
        )
    return _ss_env(
        ctx,
        SS_CMD_PC_CANDIDATE,
        {"candidate": s, "messageType": msg_type},
        session_id,
        msg_id,
    )

def build_ss_stop(ctx: Any, session_id: int, msg_id: int | None = None) -> str:
    return _ss_env(ctx, SS_CMD_PC_STOP, {"stopFrom": 1}, session_id, msg_id)

def build_ss_audio(ctx: Any, session_id: int, on: bool, msg_id: int | None = None) -> str:
    return _ss_env(ctx, SS_CMD_PC_SET_AUDIO, {"setAudio": bool(on)}, session_id, msg_id)

def build_ss_scale(ctx: Any, session_id: int, scale: int, msg_id: int | None = None) -> str:
    return _ss_env(ctx, SS_CMD_PC_SET_SCALE, {"setScale": int(scale)}, session_id, msg_id)

def build_ss_switch(
    ctx: Any,
    session_id: int,
    share_type: str,
    set_audio: bool = False,
    set_scale: int = 200,
    msg_id: int | None = None,
) -> str:
    return _ss_env(
        ctx,
        SS_CMD_PC_SWITCH,
        {"shareType": share_type, "setAudio": bool(set_audio), "setScale": int(set_scale)},
        session_id,
        msg_id,
    )

def build_ss_version(ctx: Any, session_id: int = 0, msg_id: int | None = None) -> str:
    return _ss_env(
        ctx, SS_CMD_PC_VERSION, {"key_pc_ss_version": PC_SS_VERSION}, session_id, msg_id
    )

def build_ss_reconnect(ctx: Any, session_id: int | None = None, msg_id: int | None = None) -> str:
    sid = int(session_id if session_id is not None else (getattr(ctx, "ss_session_id", 0) or 0))
    return _ss_env(ctx, SS_CMD_PC_RECONNECT, {"sessionId": sid}, sid, msg_id)

def build_ss_stats(
    ctx: Any,
    session_id: int | None = None,
    stats: dict[str, Any] | None = None,
    msg_id: int | None = None,
) -> str:
    sid = int(session_id if session_id is not None else (getattr(ctx, "ss_session_id", 0) or 0))
    payload = dict(
        stats
        or {
            "sl_fps_ave": int(getattr(ctx, "ss_fps", 30) or 30),
            "sl_br_ave_kb": 4000,
            "sl_rtt_ave_ms": 5,
        }
    )
    return _ss_env(ctx, SS_CMD_PC_STATS, payload, sid, msg_id)

def build_ss_resolution(
    ctx: Any,
    session_id: int,
    width: int,
    height: int,
    auto: int = 0,
    res_list: list[dict[str, Any]] | None = None,
    msg_id: int | None = None,
) -> str:
    payload: dict[str, Any] = {
        "setAuto": int(auto),
        "setWidth": int(width),
        "setHeight": int(height),
    }
    if res_list is not None:
        payload["setList"] = list(res_list)
    return _ss_env(ctx, SS_CMD_PC_SET_RESOLUTION, payload, session_id, msg_id)
