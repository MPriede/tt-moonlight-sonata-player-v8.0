## How it works

The song is kept in 3 levels: one-beat cells of 4 sixteenths (a note as semitones above a root, hold or rest), a phrase table of beats (cell, root, repeat) and a playlist of phrase parts with a transposition, so passages that come back, also in another key, are stored one time. The 25.175 MHz clock is divided by 252, so the player steps at 99.9 kHz (by 100 in viewer mode, or on every clock in fast mode). A tempo counter times each sixteenth (quarter = 154, as in the score). The tone counter counts up and down over each note period, so it is itself a triangle wave at the exact pitch; it is scaled per octave and set by a piano-like envelope (3 dB steps: loud at the note start, a slow decay, a quick release in rests). A first-order sigma-delta turns the sound into a 1-bit stream. A VGA block makes 640x480 at 60 Hz with a green bar that is as tall as the note is loud.

## How to test

Run it with a 25.175 MHz clock, or with a 100 kHz clock and ui[6] high. Connect the TinyVGA PMOD to the outputs and the audio PMOD (or an RC low-pass filter and an amplifier) to uio[7] or uio[0]. With all switches off the song plays and loops. ui[7] high pauses. ui[0] plays at half speed, ui[3] stops the song at the end (ui[7] high then low starts it again). In a slow VGA simulator (for example the Vulkos viewer at 20-64 % of real time) set ui[5]: the song is then 2.52x faster and higher, so it sounds near right there.

## External hardware

TinyVGA PMOD and a VGA monitor on uo[7:0]. The Tiny Tapeout audio PMOD on uio[7], or an RC low-pass filter (about 5 kHz) and a small amplifier on uio[0].
