`default_nettype none
`timescale 1ns / 1ps

/* cocotb testbench for the Moonlight player v8. It only uses the pins, so the same file works
   for the gate-level test. Everything is sampled on the rising clock edge (the gate-level
   netlist can have zero-width glitches on vsync, which a monitor does not see).
   Pin counters for the test:
     green/blue/red pixel counts, audio edges and ones, uio[7] vs uio[0] differences,
     sampled hsync/vsync falls with their period checks, a frame counter,
     lines whose green pixels are not 0 or the 128 bar pixels,
     a pitch detector on the audio pin: low-pass, mean, up-crossings (zc_count, zc_time).
*/
module tb ();

`ifndef VERILATOR
  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #2000000;
    $dumpoff;
  end
`endif

  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
`ifdef GL_TEST
  wire VPWR = 1'b1;
  wire VGND = 1'b0;
`endif

  tt_um_mpriede_moonlight_sonata_player_vga user_project (
`ifdef GL_TEST
      .VPWR(VPWR),
      .VGND(VGND),
`endif
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // TinyVGA PMOD: uo_out = {hsync, B0, G0, R0, vsync, B1, G1, R1}
  wire audio  = uio_out[0];
  wire audio7 = uio_out[7];
  wire a1     = (audio === 1'b1);   // 0 while the pin is still X (before reset): keeps the filters out of X
  wire hsync  = uo_out[7];
  wire vsync  = uo_out[3];
  wire green  = uo_out[5] & uo_out[1];
  wire red    = uo_out[4] | uo_out[0];
  wire blue   = uo_out[6] | uo_out[2];

  reg [31:0] cycle       = 0;
  reg [31:0] green_count = 0;
  reg [31:0] blue_count  = 0;
  reg [31:0] red_count   = 0;
  reg [31:0] audio_edges = 0;
  reg [31:0] audio_ones  = 0;
  reg [31:0] pin7_diff   = 0;
  reg        audio_last  = 0;
  // onsets: an edge after at least quiet_limit clocks with no edge
  reg [31:0] quiet_limit = 300;
  reg [31:0] quiet       = 0;
  reg        onset       = 0;
  // sync, sampled on the clock
  reg        hs_s = 1, vs_s = 1, h_valid = 0, v_valid = 0;
  reg [1:0]  since_rst = 0;
  reg [31:0] h_fall = 0, v_fall = 0, h_falls = 0, v_falls = 0;
  reg [31:0] h_bad = 0, v_bad = 0;     // wrong periods / pulse widths
  reg [15:0] frames = 0;               // sampled vsync falls
  // green per line: each line must have 0 or 128 green pixels (the bar)
  reg [9:0]  line_green = 0;
  reg [31:0] bar_bad = 0, bar_lines = 0;
  // pitch detector: average the pin over zc_div clocks, 3 low-pass stages, slow mean, hysteresis
  reg [15:0] zc_div = 1;
  reg [15:0] zc_n = 0;
  reg [15:0] zc_sum = 0;
  integer    y1 = 0, y2 = 0, y3 = 0, ym = 0, zx;
  reg        zc_state = 0;
  reg [31:0] zc_count = 0, zc_time = 0;

  always @(posedge clk) begin
    cycle <= cycle + 1;
    if (green) green_count <= green_count + 1;
    if (blue)  blue_count  <= blue_count + 1;
    if (red)   red_count   <= red_count + 1;
    if (audio) audio_ones  <= audio_ones + 1;
    if (audio7 != audio) pin7_diff <= pin7_diff + 1;
    if (audio != audio_last) begin
      audio_edges <= audio_edges + 1;
      if (quiet >= quiet_limit) onset <= ~onset;
      quiet <= 0;
    end else if (quiet != 32'hFFFFFFFF) begin
      quiet <= quiet + 1;
    end
    audio_last <= audio;

    // sync, sampled on the clock. The pins come from flip-flops, so the first 2 clocks after
    // reset are skipped, and the first edges after a reset start a new period count.
    if (!rst_n) begin
      since_rst <= 0; h_valid <= 0; v_valid <= 0; hs_s <= 1; vs_s <= 1; line_green <= 0;
    end else if (since_rst != 2'd3) begin
      since_rst <= since_rst + 1;
    end else begin
      hs_s <= hsync; vs_s <= vsync;
      if (hs_s && !hsync) begin
        if (h_valid && cycle - h_fall != 800) h_bad <= h_bad + 1;
        h_fall <= cycle; h_falls <= h_falls + 1; h_valid <= 1;
        if (line_green != 0 && line_green != 128) bar_bad <= bar_bad + 1;
        if (line_green == 128) bar_lines <= bar_lines + 1;
        line_green <= 0;
      end else if (green) line_green <= line_green + 1;
      if (!hs_s && hsync && h_valid && cycle - h_fall != 96) h_bad <= h_bad + 1;
      if (vs_s && !vsync) begin
        if (v_valid && cycle - v_fall != 800 * 525) v_bad <= v_bad + 1;
        v_fall <= cycle; v_falls <= v_falls + 1; frames <= frames + 1; v_valid <= 1;
      end
      if (!vs_s && vsync && v_valid && cycle - v_fall != 2 * 800) v_bad <= v_bad + 1;
    end

    // pitch detector
    if (!rst_n) begin
      y1 = 0; y2 = 0; y3 = 0; ym = 0; zc_n <= 0; zc_sum <= 0;
    end else if (zc_n + 1 >= zc_div) begin
      zx = ((zc_sum + a1) * 4096) / zc_div;
      y1 = y1 + ((zx - y1) >>> 3);
      y2 = y2 + ((y1 - y2) >>> 3);
      y3 = y3 + ((y2 - y3) >>> 3);
      ym = ym + ((y3 - ym) >>> 10);
      if (!zc_state && y3 > ym + 64) begin zc_state <= 1; zc_count <= zc_count + 1; zc_time <= cycle; end
      else if (zc_state && y3 < ym - 64) zc_state <= 0;
      zc_n <= 0; zc_sum <= 0;
    end else begin
      zc_n <= zc_n + 1; zc_sum <= zc_sum + a1;
    end
  end

endmodule
