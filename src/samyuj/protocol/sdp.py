from __future__ import annotations

import re

from samyuj.transport.discovery.usb import is_usb_ip

def order_sdp_candidate_lines(sdp: str, prefer_ip: str = "", prefer_usb: bool = False) -> list[str]:
    seen: set[tuple[str, str, str, str]] = set()
    items: list[tuple[int, int, str]] = []
    pref_p24 = prefer_ip.rsplit(".", 1)[0] if (prefer_ip and "." in prefer_ip) else ""
    for idx, line in enumerate((sdp or "").splitlines()):
        if not line.startswith("a=candidate:"):
            continue
        raw = line[2:].strip()
        f = raw.split()
        cand_proto = f[2].lower() if len(f) > 2 else ""
        cand_ip = f[4] if len(f) > 4 else ""
        cand_port = f[5] if len(f) > 5 else ""
        key = (f[0] if f else "", cand_proto, cand_ip, cand_port)
        if key in seen:
            continue
        seen.add(key)
        prio = 3
        if prefer_ip and cand_ip == prefer_ip:
            prio = 0
        elif prefer_usb and is_usb_ip(cand_ip):
            prio = 1
        elif pref_p24 and "." in cand_ip and cand_ip.rsplit(".", 1)[0] == pref_p24:
            prio = 2
        elif ":" in cand_ip or cand_ip.startswith(("192.168.122.", "172.17.", "172.18.")):
            prio = 4
        items.append((prio, idx, raw))
    items.sort(key=lambda t: (t[0], t[1]))
    return [raw for _p, _i, raw in items]

def prefer_codec_in_answer(sdp: str, codec_name: str = "H264") -> tuple[str, bool]:
    want = (codec_name or "").upper()
    if want == "AUTO" or "\nm=video" not in ("\n" + (sdp or "")):
        return sdp, False
    lines = sdp.splitlines(keepends=True)
    codec_pts: list[str] = []
    rtx_of: dict[str, str] = {}
    names: dict[str, str] = {}
    for ln in lines:
        s = ln.strip()
        if s.startswith("a=rtpmap:"):
            body = s[len("a=rtpmap:") :]
            pt, _, rest = body.partition(" ")
            names[pt] = rest.split("/")[0].upper()
        elif s.startswith("a=fmtp:"):
            body = s[len("a=fmtp:") :]
            pt, _, rest = body.partition(" ")
            m = re.search(r"apt=(\d+)", rest)
            if m and names.get(pt, "").upper() in ("RTX", ""):
                rtx_of[pt] = m.group(1)
    for pt, nm in names.items():
        if nm == want:
            codec_pts.append(pt)
    if not codec_pts:
        return sdp, False
    preferred: list[str] = []
    for pt in codec_pts:
        preferred.append(pt)
        preferred.extend(r for r, a in rtx_of.items() if a == pt)
    changed = False
    out: list[str] = []
    for ln in lines:
        s = ln.rstrip("\r\n")
        if s.startswith("m=video "):
            parts = s.split(" ")
            head, pts = parts[:3], parts[3:]
            rest = [pt for pt in pts if pt not in preferred]
            new_pts = [pt for pt in preferred if pt in pts] + rest
            if new_pts != pts:
                changed = True
            eol = ln[len(s) :]
            out.append(" ".join(head + new_pts) + eol)
        else:
            out.append(ln)
    return "".join(out), changed

def sanitize_answer_sdp(sdp: str) -> tuple[str, int]:
    lines = (sdp or "").splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.startswith("m=")]
    if not starts:
        return sdp, 0
    bounds = list(zip(starts, starts[1:] + [len(lines)]))
    drop: set[int] = set()
    for b, e in bounds:
        directions = {
            ln.strip()
            for ln in lines[b:e]
            if ln.strip() in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive")
        }
        sends = (not directions) or bool(directions & {"a=sendrecv", "a=sendonly"})
        if sends:
            continue
        for i in range(b, e):
            if lines[i].startswith(("a=ssrc:", "a=ssrc-group:", "a=msid:")):
                drop.add(i)
    if not drop:
        return sdp, 0
    return "".join(ln for i, ln in enumerate(lines) if i not in drop), len(drop)

def expand_ssrc_identity(sdp: str) -> tuple[str, int]:
    lines = (sdp or "").splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if l.strip().startswith("m=")]
    if not starts:
        return sdp, 0
    bounds = list(zip(starts, starts[1:] + [len(lines)]))
    added = 0
    for b, e in bounds:
        block = lines[b:e]
        dirs = {
            l.strip()
            for l in block
            if l.strip() in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive")
        }
        sends = (not dirs) or bool(dirs & {"a=sendrecv", "a=sendonly"})
        if not sends or any(l.strip().startswith("m=application") for l in block[:1]):
            continue
        msid = ""
        for l in block:
            t = l.strip()
            if t.startswith("a=msid:") and " " in t:
                msid = t[len("a=msid:") :].strip()
                break
        if not msid or msid == "-":
            continue
        stream, _, track = msid.partition(" ")
        track = track.strip() or stream
        newblock: list[str] = []
        for l in block:
            t = l.strip()
            newblock.append(l)
            if not t.startswith("a=ssrc:"):
                continue
            head = t[len("a=ssrc:") :]
            ssrc, _, attr = head.partition(" ")
            if not attr.startswith("cname:"):
                continue
            if any(x.strip().startswith(f"a=ssrc:{ssrc} msid:") for x in block):
                continue
            nl = "\r\n" if l.endswith("\r\n") else "\n"
            newblock.append(f"a=ssrc:{ssrc} msid:{stream} {track}" + nl)
            newblock.append(f"a=ssrc:{ssrc} mslabel:{stream}" + nl)
            newblock.append(f"a=ssrc:{ssrc} label:{track}" + nl)
            added += 1
        lines[b:e] = newblock
        shift = len(newblock) - len(block)
        bounds = [
            (bb + (shift if bb > b else 0), ee + (shift if ee > b else 0))
            for bb, ee in bounds
        ]
    return "".join(lines), added

def prefer_candidate_ip(sdp: str, ip: str) -> tuple[str, int]:
    if not ip:
        return sdp, 0
    lines = (sdp or "").splitlines(keepends=True)
    sections: list[list[str]] = [[]]
    for ln in lines:
        if ln.strip().startswith("m="):
            sections.append([])
        sections[-1].append(ln)
    touched = 0
    for sec in sections:
        cands = [i for i, l in enumerate(sec) if l.strip().startswith("a=candidate:")]
        if not cands:
            continue
        hit = [i for i in cands if f" {ip} " in sec[i]]
        if not hit:
            continue
        first = hit[0]
        if first != cands[0]:
            line = sec.pop(first)
            sec.insert(cands[0], line)
            touched += 1
        cand_parts = sec[cands[0]].strip().split()
        cand_port = cand_parts[5] if len(cand_parts) > 5 and cand_parts[5].isdigit() else None
        for i, l in enumerate(sec):
            if l.strip().startswith("m=") and cand_port:
                mp = l.strip().split()
                if len(mp) >= 4 and mp[1] != "0" and mp[1] != cand_port:
                    nl = "\r\n" if l.endswith("\r\n") else "\n"
                    mp[1] = cand_port
                    sec[i] = " ".join(mp) + nl
            elif l.strip().startswith("c=IN IP4"):
                nl = "\r\n" if l.endswith("\r\n") else "\n"
                sec[i] = f"c=IN IP4 {ip}" + nl
                break
    return "".join(l for sec in sections for l in sec), touched

def fix_answer_sdp(
    answer: str,
    offer: str,
    prefer_ip: str = "",
    codec: str = "auto",
    drop_fingerprint_algs: bool = True,
    reject_datachannel: bool = False,
    reject_audio: bool = False,
    expand_ssrc: bool = True,
) -> tuple[str, list[str]]:
    changes: list[str] = []
    if not answer:
        return answer, changes

    offer_rtpmap: dict[str, str] = {}
    offer_fmtp: dict[str, str] = {}
    in_video = False
    for ln in (offer or "").splitlines():
        ln = ln.strip()
        if ln.startswith("m="):
            in_video = ln.startswith("m=video")
            continue
        if not in_video:
            continue
        if ln.startswith("a=rtpmap:"):
            pt, _, rest = ln[len("a=rtpmap:") :].partition(" ")
            offer_rtpmap[pt.strip()] = rest.split("/")[0].strip().upper()
        elif ln.startswith("a=fmtp:"):
            pt, _, rest = ln[len("a=fmtp:") :].partition(" ")
            offer_fmtp[pt.strip()] = rest.strip()

    def apt_of(pt: str) -> str | None:
        fm = offer_fmtp.get(pt, "")
        return fm[4:].split(";")[0].strip() if fm.startswith("apt=") else None

    def base_codec(pt: str, seen: int = 0) -> str:
        name = offer_rtpmap.get(pt, "")
        if name == "RTX" and seen < 4:
            a = apt_of(pt)
            return base_codec(a, seen + 1) if a else ""
        return name

    a_lines = answer.splitlines(keepends=True)
    out: list[str] = []
    section_is_video = False
    kept_pts: set[str] = set()

    for ln in a_lines:
        s = ln.strip()
        nl = "\r\n" if ln.endswith("\r\n") else "\n"

        if s.startswith("m="):
            kept_pts = set()
            parts = s.split()
            if (reject_datachannel and s.startswith("m=application")) or (
                reject_audio and s.startswith("m=audio")
            ):
                parts[1] = "0"
                out.append(" ".join(parts) + nl)
                changes.append(f"{parts[0][2:]} rejected (port 0)")
                section_is_video = False
                continue
            section_is_video = s.startswith("m=video")
            if section_is_video and codec in ("vp8", "h264"):
                want = {"vp8": ("VP8", "VP9"), "h264": ("H264",)}[codec]
                keep = [pt for pt in parts[3:] if base_codec(pt) in want]
                if keep and keep != parts[3:]:
                    changes.append(
                        f"video payload types filtered to {codec}: "
                        f"{' '.join(parts[3:])} -> {' '.join(keep)}"
                    )
                    parts = parts[:3] + keep
                kept_pts = set(keep or parts[3:])
                out.append(" ".join(parts) + nl)
                continue
            out.append(ln)
            continue

        if drop_fingerprint_algs and s.startswith("a=fingerprint:"):
            if not s.startswith("a=fingerprint:sha-256"):
                changes.append(f"dropped {s.split(':', 1)[1].split()[0]} fingerprint")
                continue

        if section_is_video and s.startswith("a=fmtp:"):
            pt, _, rest = s[len("a=fmtp:") :].partition(" ")
            pt = pt.strip()
            if kept_pts and pt not in kept_pts:
                continue
            want_fmtp = offer_fmtp.get(pt)
            if want_fmtp is not None and want_fmtp != rest.strip():
                changes.append(
                    f"fmtp:{pt} restored to the offer's value ({rest.strip()!r} -> {want_fmtp!r})"
                )
                out.append(f"a=fmtp:{pt} {want_fmtp}" + nl)
                continue
            out.append(ln)
            continue

        if section_is_video and kept_pts and s.startswith("a=rtpmap:"):
            if s[len("a=rtpmap:") :].split(" ")[0].strip() not in kept_pts:
                continue
        if section_is_video and kept_pts and s.startswith("a=rtcp-fb:"):
            if s[len("a=rtcp-fb:") :].split(" ")[0].strip() not in kept_pts:
                continue
        out.append(ln)

    sdp_out = "".join(out)
    if expand_ssrc:
        sdp_out, n = expand_ssrc_identity(sdp_out)
        if n:
            changes.append(
                f"added msid/mslabel/label to {n} ssrc line(s) (libwebrtc receive-stream shape)"
            )
    if prefer_ip:
        sdp_out, n = prefer_candidate_ip(sdp_out, prefer_ip)
        if n:
            changes.append(f"ICE candidates / c= now prefer {prefer_ip} ({n} section(s))")
    return sdp_out, changes
