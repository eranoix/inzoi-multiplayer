"""Line up the two games: what each player was really doing next to what the other one's game showed.

    python tools/telemetry_report.py [--name Chey] [--minutes 15] [--mine FILE] [--partner FILE] [--out FILE]

Reads this PC's telemetry (client/telemetry.py) and the partner's (fetched with POST /partner/telemetry, saved as
%APPDATA%/inzoi-mp/partner-<name>-telemetry.jsonl). For each direction (partner -> here, here -> partner):
- the clocks are lined up (the receiver's arrival time minus the sender's "ts", median),
- every sample of the Zoi as the receiving game shows it is compared with what the sender's game was doing at that
  moment: distance, same lot, same action (interaction bag), same motion,
- and the receiver's controller reports around each mismatch say why (no such object here, placed, stopped...).
Prints a summary, the actions that didn't come out right (with the reasons), and a timeline of the differences.
"""
import argparse
import bisect
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path

CFG = Path(os.environ.get("APPDATA", Path.home())) / "inzoi-mp"


def load(path: Path, since: float | None):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows.append(r)
    except OSError:
        pass
    if since is not None and rows:
        last = max(r["t"] for r in rows)
        rows = [r for r in rows if r["t"] >= last - since]
    return rows


def offset(receiver_rows, sender_name=None):
    """Receiver clock - sender clock (from arrival time vs the state's ts; includes the network delay)."""
    d = [r["t"] - r["st"]["ts"] for r in receiver_rows if r["k"] == "in" and isinstance(r.get("st"), dict)
         and isinstance(r["st"].get("ts"), (int, float)) and (sender_name is None or r.get("p") == sender_name)]
    return statistics.median(d) if d else 0.0


def bag_of(act):
    return act.get("bag") if isinstance(act, dict) else None


def fmt_t(t, t0):
    return f"{t - t0:7.1f}s"


def direction(title, sender_rows, receiver_rows, sender_name, out):
    """sender's own states ("out") = the truth; receiver's samples of the sender's Zoi ("pup") = what was shown."""
    off = offset(receiver_rows, sender_name)
    truth = sorted((((r["st"].get("ts") or r["t"]) + off, r["st"]) for r in sender_rows
                    if r["k"] == "out" and isinstance(r.get("st"), dict) and "x" in r["st"]), key=lambda x: x[0])
    pups = [r for r in receiver_rows if r["k"] == "pup" and (sender_name is None or r.get("p") == sender_name)]
    luas = [r for r in receiver_rows if r["k"] in ("lua", "note")]
    lua_t = [r["t"] for r in luas]
    out.append(f"\n=== {title} ===")
    if not truth or not pups:
        out.append(f"  not enough data (sender states: {len(truth)}, samples of their Zoi: {len(pups)})")
        return
    out.append(f"  clocks: receiver - sender = {off:+.2f} s (with the network delay); "
               f"{len(truth)} states sent, {len(pups)} samples of their Zoi")
    tt = [t for t, _ in truth]
    t0 = pups[0]["t"]
    errs, site_bad, act_total, act_ok = [], 0, 0, 0
    mot_total, mot_ok = 0, 0
    look_total, look_ok = 0, 0
    wrong = Counter()
    wrong_as = defaultdict(Counter)
    reasons = defaultdict(Counter)
    timeline = []
    last_line = None
    for p in pups:
        i = bisect.bisect_right(tt, p["t"]) - 1
        if i < 0:
            continue
        st = truth[i][1]
        err = math.hypot(p["x"] - st["x"], p["y"] - st["y"])
        errs.append(err)
        if st.get("site") is not None and p.get("site") is not None and int(st["site"]) != int(p["site"]):
            site_bad += 1
        tb = bag_of(st.get("act"))
        want_m = {m.get("id") for m in st.get("motions") or [] if isinstance(m, dict)}
        motions_ok = bool(want_m) and want_m <= set(p.get("motions") or [])
        if want_m:
            mot_total += 1
            mot_ok += motions_ok
        shown = p.get("bag") or "-"
        if tb:
            act_total += 1
            if shown == tb or (shown or "").startswith(tb) or tb.startswith(shown or "\0"):
                act_ok += 1
                verdict = "ok"
            else:
                wrong[tb] += 1
                wrong_as[tb][shown] += 1
                j = bisect.bisect_left(lua_t, p["t"] - 6)
                for r in luas[j:j + 40]:
                    if r["t"] > p["t"] + 1:
                        break
                    text = r.get("text", "")
                    if tb in text or "placed" in text or "on its own" in text or "no " in text:
                        reasons[tb][text.split(": ", 1)[-1][:110]] += 1
                verdict = "WRONG ACTION"
        else:
            verdict = "ok" if not p.get("bag") or (p.get("bag") or "").startswith(("ClickMove", "Move")) else "EXTRA ACTION"
        if tb or want_m:
            look_total += 1
            look_ok += (verdict == "ok" or motions_ok) and err <= 100
        pos = "far" if err > 150 else ""
        line = (f"{fmt_t(p['t'], t0)}  real: {tb or 'walking/idle':38.38} anim {str(st.get('anim') or '-'):28.28}"
                f" | shown: {shown:38.38} anim {str(p.get('anim') or '-'):28.28} | {err / 100:5.1f} m  "
                f"{verdict if verdict != 'ok' else ''} {pos}")
        key = (tb, shown, verdict, pos)
        if key != last_line:
            timeline.append(line)
            last_line = key
    errs.sort()
    pct = lambda q: errs[min(len(errs) - 1, int(len(errs) * q))] / 100
    out.append(f"  position: median {pct(0.5):.2f} m, 90% under {pct(0.9):.2f} m, worst {errs[-1] / 100:.1f} m; "
               f"within 1 m {100 * sum(e <= 100 for e in errs) / len(errs):.0f}% of the time; "
               f"on another lot {100 * site_bad / len(errs):.0f}%")
    if look_total:
        out.append(f"  looks the same (same action, or every animation of theirs playing, within 1 m): "
                   f"{100 * look_ok / look_total:.0f}% of the time they were doing something")
    if mot_total:
        out.append(f"  poses: every animation they played was playing here {100 * mot_ok / mot_total:.0f}% of the time")
    if act_total:
        out.append(f"  actions: the same action shown {100 * act_ok / act_total:.0f}% of the time they were doing one")
    if wrong:
        out.append("  actions that didn't come out right (samples, what showed instead, why):")
        for b, n in wrong.most_common(25):
            shown = ", ".join(f"{k} x{v}" for k, v in wrong_as[b].most_common(3))
            why = "; ".join(f"{k} x{v}" for k, v in reasons[b].most_common(3)) or "(no report from the controller)"
            out.append(f"    {b}: {n} -> shown {shown}\n        why: {why}")
    lua_kinds = Counter()
    for r in luas:
        text = r.get("text", "")
        for k in ("placed where they are", "no ", "on its own", "act:", "act_stop", "adopt", "despawn"):
            if k in text:
                lua_kinds[k.strip()] += 1
                break
    if lua_kinds:
        out.append("  receiver's controller: " + ", ".join(f"{k} x{v}" for k, v in lua_kinds.most_common()))
    out.append("  timeline (only when something changes):")
    out.extend("    " + x for x in timeline[-400:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Chey")
    ap.add_argument("--me", default="Arthur", help="this player's name as the partner's game knows it")
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--mine", default=str(CFG / "telemetry.jsonl"))
    ap.add_argument("--partner")
    ap.add_argument("--out")
    a = ap.parse_args()
    partner = Path(a.partner or CFG / f"partner-{a.name}-telemetry.jsonl")
    since = a.minutes * 60
    mine, theirs = load(Path(a.mine), since), load(partner, since)
    out = [f"telemetry: here {len(mine)} lines, {a.name} {len(theirs)} lines (last {a.minutes:.0f} min)"]
    direction(f"{a.name} -> here: what {a.name} did vs. what this game showed", theirs, mine, a.name, out)
    direction(f"here -> {a.name}: what you did vs. what {a.name}'s game showed", mine, theirs, a.me, out)
    text = "\n".join(out)
    print(text)
    if a.out:
        Path(a.out).write_text(text, "utf-8")


if __name__ == "__main__":
    main()
