# SPDX-License-Identifier: Apache-2.0
# Tests for the Moonlight Sonata player v8 (VGA, 1x1): bars 1-37 and 200-201 of the 3rd movement,
# 388 notes in 624 sixteenths (61 s).
#   VGA mode (ui[6] = 0): 25.175 MHz clock, the player steps on one clock in 252 (in 100 with ui[5]).
#   Fast mode (ui[6] = 1): 100 kHz board clock, the player steps on every clock.
# The tests use ONLY the pins (through the pin counters in tb.v), so they also run on the
# gate-level netlist (make GATES=yes). Sync is sampled on the clock edge.
# Pitch: tb.v low-passes the audio pin and counts up-crossings; the median crossing interval
# inside each note is the note period.
# GATES=yes runs the long tests on a part of the song (FULL_GL=1: the whole song).

import json
import os
import statistics
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Edge, Timer
from cocotb.utils import get_sim_time

EXP = json.load(open(os.path.join(os.path.dirname(__file__), "expected.json")))
HALF, LENGTH, SLOT = EXP["half"], EXP["length"], EXP["slot"]
CODES = EXP["slot_codes"]
NOTES, SLOTS = len(HALF), EXP["slots"]
GL = os.environ.get("GATES", "no") == "yes"
FULL = (not GL) or os.environ.get("FULL_GL", "0") == "1"
VGA_PS, FAST_PS = 39722, 10_000_000      # 25.175 MHz and 100 kHz
SIX = {0: 9740, 1: 19480}                # ticks in a sixteenth: normal, half speed (ui[0])
PAUSE, FAST, VIEWER, LOOP_OFF, HALF_SPEED = 0x80, 0x40, 0x20, 0x08, 0x01
LINE, FRAME = 800, 800 * 525
TOL = 0.025                              # pitch tolerance (2.5 %; a semitone is 5.9 %)


class Run:
    period = FAST_PS
    div = 1
    t0 = 0
    dut = None

    @classmethod
    def clocks(cls):
        return get_sim_time("ps") / cls.period

    @classmethod
    def ticks(cls):
        return cls.clocks() / cls.div


async def wait_ticks(n):
    """Wait n player ticks (n x div clocks)."""
    n = int(round(n * Run.div))
    if n > 0:
        await Timer(n * Run.period, unit="ps")


def count(sig):
    return int(sig.value)


async def start(dut, ui=FAST, clock=True):
    """Reset with the switches at ui. The clock is started once per test."""
    Run.dut = dut
    if clock:
        Run.period = FAST_PS if ui & FAST else VGA_PS
        cocotb.start_soon(Clock(dut.clk, Run.period, unit="ps").start())
    Run.div = 1 if ui & FAST else (100 if ui & VIEWER else 252)
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.ui_in.value = ui
    dut.quiet_limit.value = 350 * Run.div
    dut.zc_div.value = Run.div
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    Run.t0 = Run.ticks()           # the player starts here (sixteenth 0)


class Crossings:
    """Collects the times (ticks) of the pitch detector's up-crossings on the audio pin."""
    def __init__(self, dut):
        self.t = []
        self.dut = dut
        self.task = cocotb.start_soon(self.run())

    async def run(self):
        while True:
            await Edge(self.dut.zc_count)
            self.t.append(Run.ticks())

    def period(self, t0, t1):
        z = [x for x in self.t if t0 <= x <= t1]
        if len(z) < 4:
            return None
        d = [b - a for a, b in zip(z, z[1:])]
        m = statistics.median(d)
        good = [x for x in d if abs(x - m) < 0.1 * m]
        return sum(good) / len(good)


def check_note(zc, n, six=SIX[0], base=None):
    """Pitch of note n from the crossings inside it (after the attack, before the end)."""
    base = Run.t0 if base is None else base
    a = base + SLOT[n] * six + 1500
    b = base + (SLOT[n] + LENGTH[n]) * six - 200
    p = zc.period(a, b)
    exp = 2 * HALF[n]
    return p is not None and abs(p - exp) <= TOL * exp, p, exp


async def next_frame(dut):
    f = count(dut.frames)
    while count(dut.frames) == f:
        await Edge(dut.frames)


async def green_in_frames(dut, frames):
    await next_frame(dut)
    g = count(dut.green_count)
    for _ in range(frames):
        await next_frame(dut)
    return count(dut.green_count) - g


@cocotb.test()
async def test_project(dut):
    """Template check: no X or Z on the outputs after reset, audio pins are outputs."""
    await start(dut, 0x00)
    await ClockCycles(dut.clk, 100)
    assert dut.uo_out.value.is_resolvable and dut.uio_out.value.is_resolvable
    assert int(dut.uio_oe.value) == 0x81, "uio[0] and uio[7] must be outputs"
    assert int(dut.uio_out.value) & 0x7E == 0


@cocotb.test()
async def test_vga_sync(dut):
    """VGA mode, sampled on the clock: hsync period 800 clocks, low 96; vsync period 800 x 525,
    low 2 lines; 3 frames. Blue background, no red."""
    await start(dut, 0x00)
    f0, h0, hb, vb = count(dut.frames), count(dut.h_falls), count(dut.h_bad), count(dut.v_bad)
    while count(dut.frames) < f0 + 4:
        await next_frame(dut)
    assert count(dut.h_falls) - h0 >= 3 * 525
    assert count(dut.h_bad) == hb, f"{count(dut.h_bad) - hb} wrong hsync periods or pulses"
    assert count(dut.v_bad) == vb, f"{count(dut.v_bad) - vb} wrong vsync periods or pulses"
    assert count(dut.blue_count) > 0, "the background must be blue"
    assert count(dut.red_count) == 0, "no red pixels"


@cocotb.test()
async def test_paused_is_silent(dut):
    """VGA mode, pause on: the audio pins stay 0 and there is no green bar, the picture runs."""
    await start(dut, PAUSE)
    e = count(dut.audio_edges)
    await ClockCycles(dut.clk, 5000)
    b = count(dut.blue_count)
    assert await green_in_frames(dut, 2) == 0, "no green bar while paused"
    assert count(dut.blue_count) > b
    assert count(dut.audio_edges) == e and int(dut.uio_out.value) == 0


@cocotb.test()
async def test_bar_and_envelope(dut):
    """Fast mode. Sixteenth 0 is a rest: silent. Note 0 starts loud; a long note decays.
    A rest is silent. The green bar is 128 pixels wide on every lit line."""
    await start(dut, FAST)
    e0, bb0, bl0 = count(dut.audio_edges), count(dut.bar_bad), count(dut.bar_lines)
    await wait_ticks(SIX[0] - 100)
    assert count(dut.audio_edges) == e0, "silent in the first rest"

    async def density(ticks):
        o = count(dut.audio_ones)
        await wait_ticks(ticks)
        return (count(dut.audio_ones) - o) / ticks

    await wait_ticks(Run.t0 + SIX[0] + 50 - Run.ticks())
    loud = await density(2000)
    n = next(i for i, l in enumerate(LENGTH) if l >= 4)
    await wait_ticks(Run.t0 + SLOT[n] * SIX[0] + 50 - Run.ticks())
    attack = await density(2000)
    await wait_ticks(Run.t0 + (SLOT[n] + LENGTH[n]) * SIX[0] - 2500 - Run.ticks())
    late = await density(2000)
    dut._log.info(f"density: note 0 {loud:.3f}, note {n} attack {attack:.3f}, late {late:.3f}")
    assert loud > 0.2 and attack > 0.2, "notes must start loud"
    assert late < 0.8 * attack, "a long note must decay"
    r = next(i for i, c in enumerate(CODES) if c == "R" and i > SLOT[n])
    await wait_ticks(Run.t0 + r * SIX[0] + 1000 - Run.ticks())
    e = count(dut.audio_edges)
    await wait_ticks(SIX[0] - 1200)
    assert count(dut.audio_edges) == e, f"sixteenth {r} is a rest: no sound"
    # a line can be cut where the level changes inside it (a note start or a decay step)
    bb, bl = count(dut.bar_bad) - bb0, count(dut.bar_lines) - bl0
    dut._log.info(f"bar: {bl} lit lines of 128 pixels, {bb} cut lines")
    assert bl > 0, "the bar must light"
    assert bb <= max(4, bl // 100), f"{bb} lines with a wrong bar"
    assert count(dut.pin7_diff) == 0, "uio_out[7] must be the same as uio_out[0]"


@cocotb.test()
async def test_first_notes(dut):
    """Fast mode, loop off: the pitch of the first 30 notes on the audio pin."""
    await start(dut, FAST | LOOP_OFF)
    zc = Crossings(dut)
    await wait_ticks(Run.t0 + (SLOT[29] + LENGTH[29]) * SIX[0] - Run.ticks())
    zc.task.cancel()
    for n in range(30):
        ok, p, exp = check_note(zc, n)
        assert ok, f"note {n}: period {p} ticks, expected {exp}"


@cocotb.test()
async def test_half_speed(dut):
    """ui[0] = half speed: note 0 (G#2) still sounds 1.5 sixteenths after its start, where at
    normal speed note 1 (C#3) sounds. The switches are reset, so the first sixteenth after reset
    (a rest) is a normal one at both speeds."""
    for s in (0, 1):
        await start(dut, FAST | LOOP_OFF | (HALF_SPEED if s else 0), clock=(s == 0))
        zc = Crossings(dut)
        t = Run.t0 + SIX[0] + 1.5 * SIX[0]
        await wait_ticks(t + 4000 - Run.ticks())
        zc.task.cancel()
        p = zc.period(t, t + 4000)
        exp = 2 * HALF[0] if s else 2 * HALF[1]
        assert p and abs(p - exp) <= TOL * exp, f"speed {s}: period {p}, expected {exp}"


@cocotb.test()
async def test_viewer_mode(dut):
    """VGA clock with ui[5] (viewer): the player steps on 1 clock in 100, so the opening rest
    is 9740 x 100 clocks, and note 0 has a period of 2 x 481 x 100 clocks."""
    await start(dut, VIEWER | LOOP_OFF)
    e = count(dut.audio_edges)
    await wait_ticks(SIX[0] - 200)
    assert count(dut.audio_edges) == e, "rest at sixteenth 0"
    zc = Crossings(dut)
    a = Run.t0 + SIX[0] + 1500
    await wait_ticks(a + 6 * 2 * HALF[0] - Run.ticks())
    zc.task.cancel()
    p = zc.period(a, Run.ticks())
    assert p and abs(p - 2 * HALF[0]) <= TOL * 2 * HALF[0], f"period {p} x 100 clocks, expected {2 * HALF[0]}"


@cocotb.test()
async def test_full_song_and_restart(dut):
    """Fast mode, loop off: the pitch of every note on the pin, every rest silent, no sound
    after the end. Then pause on and off starts the song again at note 0."""
    await start(dut, FAST | LOOP_OFF)
    zc = Crossings(dut)
    nn = NOTES if FULL else 60
    last = SLOT[nn - 1] + LENGTH[nn - 1]
    rest_bad = []
    rests = [s for s in range(last) if CODES[s] == "R"]
    for s in rests:
        await wait_ticks(Run.t0 + s * SIX[0] + 1000 - Run.ticks())
        e = count(dut.audio_edges)
        await wait_ticks(SIX[0] - 1200)
        if count(dut.audio_edges) != e:
            rest_bad.append(s)
    await wait_ticks(Run.t0 + last * SIX[0] - Run.ticks())
    bad = []
    for n in range(nn):
        ok, p, exp = check_note(zc, n)
        if not ok:
            bad.append((n, p, exp))
    assert not bad, f"{len(bad)} wrong notes, first: {bad[:5]}"
    assert not rest_bad, f"sound in rests at sixteenths {rest_bad[:5]}"
    dut._log.info(f"{nn} notes checked on the pin, {len(rests)} rests silent")
    if not FULL:
        zc.task.cancel()
        return
    await wait_ticks(Run.t0 + SLOTS * SIX[0] + 2000 - Run.ticks())
    e = count(dut.audio_edges)
    await wait_ticks(20000)
    assert count(dut.audio_edges) == e and int(dut.uio_out.value) == 0, "no sound after the end"
    assert count(dut.pin7_diff) == 0
    dut.ui_in.value = FAST | LOOP_OFF | PAUSE
    await ClockCycles(dut.clk, 10)
    dut.ui_in.value = FAST | LOOP_OFF
    base = Run.ticks()
    await wait_ticks(base + (SLOT[1] + 1) * SIX[0] - Run.ticks())
    zc.task.cancel()
    for n in (0, 1):
        ok, p, exp = check_note(zc, n, base=base)
        assert ok, f"after the restart, note {n}: period {p}, expected {exp}"


@cocotb.test(skip=GL and not FULL)
async def test_loop_on_continues(dut):
    """Fast mode, loop on: after the last sixteenth the song starts again with note 0."""
    await start(dut, FAST)
    base = Run.t0 + SLOTS * SIX[0]
    await wait_ticks(base - SIX[0] - Run.ticks())
    zc = Crossings(dut)
    await wait_ticks(base + (SLOT[1] + 1) * SIX[0] - Run.ticks())
    zc.task.cancel()
    for n in (0, 1):
        ok, p, exp = check_note(zc, n, base=base)
        assert ok, f"after the loop, note {n}: period {p}, expected {exp}"


@cocotb.test()
async def test_pause_holds_place(dut):
    """Fast mode. Pause inside note 5: no sound and no bar. Play again: the rest of note 5 is
    silent (no click), and note 6 comes at the right time with its pitch."""
    await start(dut, FAST)
    await wait_ticks(Run.t0 + SLOT[5] * SIX[0] + 1000 - Run.ticks())
    dut.ui_in.value = FAST | PAUSE
    await ClockCycles(dut.clk, 400)          # release
    e, g = count(dut.audio_edges), count(dut.green_count)
    await wait_ticks(20000)
    assert count(dut.audio_edges) == e and int(dut.uio_out.value) == 0, "no sound while paused"
    assert count(dut.green_count) == g, "no bar while paused"
    dut.ui_in.value = FAST
    resume = Run.ticks()
    await wait_ticks(SIX[0] - 1000 - 50)
    assert count(dut.audio_edges) == e, "the rest of the paused note must stay silent (no click)"
    zc = Crossings(dut)
    base = resume + (SIX[0] - 1000) - SLOT[6] * SIX[0]
    await wait_ticks(base + (SLOT[6] + LENGTH[6]) * SIX[0] - Run.ticks())
    zc.task.cancel()
    ok, p, exp = check_note(zc, 6, base=base)
    assert ok, f"note 6 after the pause: period {p}, expected {exp}"
