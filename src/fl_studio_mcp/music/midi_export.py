"""Build a type-1 multi-track .mid from an arrangement spec (uses mido, a CORE
dependency -- no extra install). Bulk-arrangement path: Claude supplies the
whole arrangement, this writes ONE Standard MIDI File the user imports into FL.

Spec: tracks = [{"name", "channel"(0-15), "notes": [{"pitch", "start_bars",
"length_bars", "velocity"}]}]. Times are in BARS (like the note bridge);
velocity is 0..1 (0.787 ~= MIDI 100) or a raw 1..127 int. PURE except for the
final save in write_midi(). read_midi() goes the other way, for a .mid exported
from FL.
"""
from __future__ import annotations

import os


def _vel(v):
    v = float(v)
    out = int(round(v * 127)) if v <= 1.0 else int(round(v))
    return max(1, min(127, out))


def build_midi(tracks, bpm, ppq=480, beats_per_bar=4):
    """Spec -> mido.MidiFile (type 1). A conductor track holds tempo + time-sig;
    each spec track becomes one MIDI track. Absolute-tick events are sorted
    (note-offs before note-ons at a tie) then emitted as deltas."""
    import mido
    ticks_per_bar = int(ppq * beats_per_bar)
    mf = mido.MidiFile(type=1, ticks_per_beat=ppq)

    cond = mido.MidiTrack()
    mf.tracks.append(cond)
    cond.append(mido.MetaMessage("track_name", name="Tempo", time=0))
    cond.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(float(bpm)), time=0))
    cond.append(mido.MetaMessage("time_signature", numerator=int(beats_per_bar),
                                 denominator=4, time=0))
    cond.append(mido.MetaMessage("end_of_track", time=0))

    for t in tracks:
        tr = mido.MidiTrack()
        mf.tracks.append(tr)
        tr.append(mido.MetaMessage("track_name", name=str(t.get("name") or "Track")[:40], time=0))
        ch = int(t.get("channel", 0)) & 0x0F
        events = []                                   # (abs_tick, order, msg)
        for n in t.get("notes", []):
            pitch = max(0, min(127, int(n["pitch"])))
            vel = _vel(n.get("velocity", 0.787))
            st = max(0, int(round(float(n["start_bars"]) * ticks_per_bar)))
            dur = max(1, int(round(float(n["length_bars"]) * ticks_per_bar)))
            events.append((st, 1, mido.Message("note_on", note=pitch, velocity=vel, channel=ch)))
            events.append((st + dur, 0, mido.Message("note_off", note=pitch, velocity=0, channel=ch)))
        events.sort(key=lambda e: (e[0], e[1]))       # off before on at a tie
        prev = 0
        for abs_t, _order, msg in events:
            msg.time = abs_t - prev
            prev = abs_t
            tr.append(msg)
        tr.append(mido.MetaMessage("end_of_track", time=0))
    return mf


def write_midi(tracks, bpm, path, ppq=480, beats_per_bar=4):
    """Build + save the .mid to ``path`` (creating parent dirs). Returns the
    mido.MidiFile (for readback/inspection)."""
    mf = build_midi(tracks, bpm, ppq=ppq, beats_per_bar=beats_per_bar)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    mf.save(path)
    return mf


def read_midi(path, start=0, page=500):
    """Read a .mid into note-bridge notes, one page at a time.

    Returns notes grouped per (track, channel), each note {pitch, time_bars,
    length_bars, velocity 0..1}. Raises on an unreadable file.

    A bar here is always 4 quarter notes, whatever the time signature: that is
    what the note bridge multiplies by (score.PPQ * 4), so a note written back
    lands on the same tick. Real time signatures are reported separately.
    """
    import mido
    mf = mido.MidiFile(path)
    ppq = mf.ticks_per_beat
    if ppq <= 0:
        raise ValueError("SMPTE or zero time division is not supported (%d)" % ppq)

    def bars(ticks):
        return round(ticks / (ppq * 4), 6)

    groups, names, sigs = {}, {}, set()
    unterminated = 0

    def close(index, key, on, end):
        on_tick, velocity = on
        groups.setdefault((index, key[0]), []).append({
            "pitch": key[1],
            "time_bars": bars(on_tick),
            "length_bars": bars(max(1, end - on_tick)),   # the bridge rejects 0
            "velocity": round(velocity / 127, 4),
        })

    for index, track in enumerate(mf.tracks):
        names[index] = track.name
        tick = 0
        held = {}                     # (channel, pitch) -> ons, oldest first
        for msg in track:
            tick += msg.time
            if msg.type == "time_signature":
                sigs.add((tick, msg.numerator, msg.denominator))
            elif msg.type == "note_on" and msg.velocity > 0:
                held.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type in ("note_on", "note_off"):
                ons = held.get((msg.channel, msg.note))
                if ons:               # an off with nothing held is ignored
                    close(index, (msg.channel, msg.note), ons.pop(0), tick)
        for key, ons in held.items():
            unterminated += len(ons)
            for on in ons:
                close(index, key, on, tick)

    total = sum(len(notes) for notes in groups.values())
    tracks = []
    skip, room = start, page
    for (index, channel), notes in sorted(groups.items()):
        notes.sort(key=lambda n: (n["time_bars"], n["pitch"]))
        taken = notes[skip:skip + room]
        skip = max(0, skip - len(notes))
        room -= len(taken)
        if taken:
            tracks.append({"track": index, "name": names[index], "channel": channel,
                           "note_count": len(notes), "notes": taken})

    # shortcut: lists only the first 32 changes, raise it if a file ever needs more.
    time_signatures = [{"time_bars": bars(t), "numerator": n, "denominator": d}
                       for t, n, d in sorted(sigs)[:32]]
    return {
        "format": mf.type,
        "ticks_per_beat": ppq,
        "time_signatures": time_signatures or [{"time_bars": 0.0, "numerator": 4, "denominator": 4}],
        "time_signature_count": len(sigs),
        "total_notes": total,
        "unterminated_notes": unterminated,
        "start": start,
        "next_start": start + page if start + page < total else None,
        "tracks": tracks,
    }
