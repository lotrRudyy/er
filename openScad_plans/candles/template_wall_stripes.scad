// OpenSCAD 2021.01
// A4 WRAP STRIPS ONLY (baked constellation_id = 16)
//
// OUTPUT:
// - wall_mode=0: bottom wall strips
// - wall_mode=1: top wall strips
// - Use wrap_page = 0,1,2.. if there are more segments than fit.
//
// PRINT RULE: Export as SVG (recommended), print at 100% (no fit-to-page).
// ASCII ONLY.

$fn = 96;

// ============================================================
// USER INPUT (measured build dimensions)
// ============================================================

wrap_page = 0;
wall_mode = 0; // 0 = bottom, 1 = top

// Disk diameters (mm)
token_bottom_d = 184;
token_top_d    = 140;

// Wall heights (mm)
token_bottom_h = 20;
token_top_h    = 20;

// 🔥 REAL puzzle mark width — NOW 5× THICKER 🔥
wall_mark_w = 0.425 * 5;   // 2.125mm

// Wrap tiling
wrap_overlap = 8;
wrap_gap_y   = 8;
wrap_margin  = 25;

// Page size (A4 portrait, mm)
page_w = 210;
page_h = 297;

// Internal layout base coords (A4 landscape, mm)
base_page_w = 297;
base_page_h = 210;

// ============================================================
// BAKED DATA (constellation_id = 16)
// ============================================================

global_top_marks = [
  13, 80, 97, 210, 227, 60, 185, 170, 260, 290, 300, 340
];

global_bottom_marks = [
  340, 5, 15, 55, 145, 93, 97, 110, 115, 180, 200, 208, 240, 250, 265, 290
];

// Alignment markers (FIXED, world angles)
top_pointer_ang_deg = 90.0;

bottom_candle_angles_deg = [
  272.0, // C0
  49.0,  // C1
  126.0, // C2
  329.0  // C3
];

// ============================================================
// HELPERS
// ============================================================
function _deg_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function _circumference(d) = PI * d;
function _ang_to_x(ang_deg, circ_mm) = circ_mm * (_deg_norm(ang_deg) / 360);

function usable_w() = base_page_w - 2*wrap_margin;
function core_w()   = usable_w() - wrap_overlap;
function seg_step() = core_w();
function seg_count(circ_mm) = ceil(circ_mm / seg_step());
function seg_x0(seg) = seg * seg_step();
function seg_x1(seg, circ_mm) = min(seg_x0(seg) + core_w(), circ_mm);

// ============================================================
// 2D DRAW PRIMITIVES
// ============================================================

ui_line_w = 0.15;

font_name = "Liberation Sans";
title_size = 5;
info_size  = 3.2;

module label(txt, sz=6) {
  text(txt, size=sz, font=font_name, halign="left", valign="baseline");
}

module line2d(x0,y0,x1,y1,w=ui_line_w) {
  dx = x1-x0;
  dy = y1-y0;
  L = sqrt(dx*dx + dy*dy);
  ang = atan2(dy, dx);
  translate([x0,y0]) rotate(ang)
    translate([0,-w/2]) square([max(0.01,L), w], center=false);
}

module rect_outline_lines(w, h, lw=ui_line_w) {
  line2d(0,0,w,0,lw);
  line2d(0,h,w,h,lw);
  line2d(0,0,0,h,lw);
  line2d(w,0,w,h,lw);
}

module align_tick(x_center, h_mm, label_txt="") {
  tick_w = 0.8;
  tick_h = 2.2;
  translate([x_center - tick_w/2, h_mm])
    square([tick_w, tick_h], center=false);
  if (label_txt != "")
    translate([x_center + 1.0, h_mm + tick_h + 0.3])
      label(label_txt, info_size);
}

module scale_bar_50mm(x, y) {
  translate([x,y]) {
    square([50,0.6], center=false);
    translate([0,2.5]) label("50mm", info_size);
  }
}

module page_border_portrait_centered() {
  translate([-page_w/2, -page_h/2])
    rect_outline_lines(page_w, page_h);
}

module overlap_field_end(overlap_w, h_mm) {
  rect_outline_lines(overlap_w, h_mm);
  line2d(0,0,0,h_mm);
}

// ============================================================
// WRAP SEGMENT
// ============================================================
module wrap_segment_base(
  d_mm, h_mm, marks_angles,
  seg, segN,
  align_angles_deg = [],
  align_labels     = []
) {
  circ = _circumference(d_mm);
  x0 = seg_x0(seg);
  x1 = seg_x1(seg, circ);
  core = x1 - x0;

  flap = min(wrap_overlap, usable_w() - core);
  w    = core + flap;

  rect_outline_lines(w, h_mm);

  if (flap > 0.01)
    translate([core,0]) overlap_field_end(flap, h_mm);

  // 🔥 REAL PUZZLE MARKS (NOW FAT AS HELL) 🔥
  for (a = marks_angles) {
    xm = _ang_to_x(a, circ);
    if (xm >= x0 && xm <= x1)
      translate([xm - x0 - wall_mark_w/2, 0])
        square([wall_mark_w, h_mm], center=false);
  }

  for (k = [0:len(align_angles_deg)-1]) {
    xa = _ang_to_x(align_angles_deg[k], circ);
    if (xa >= x0 && xa <= x1)
      align_tick(xa - x0, h_mm, align_labels[k]);
  }
}

// ============================================================
// PAGE LAYOUT
// ============================================================
module wrap_single_page_base(mode) {
  is_top = (mode == 1);
  d_mm   = is_top ? token_top_d : token_bottom_d;
  h_mm   = is_top ? token_top_h : token_bottom_h;
  marks  = is_top ? global_top_marks : global_bottom_marks;

  circ = _circumference(d_mm);
  nseg = seg_count(circ);

  scale_bar_50mm(base_page_w - wrap_margin - 70, base_page_h - wrap_margin);

  y_start = base_page_h - wrap_margin - 30;

  for (s=[0:nseg-1]) {
    y = y_start - s*(h_mm + wrap_gap_y);
    aA = is_top ? [top_pointer_ang_deg] : bottom_candle_angles_deg;
    aL = is_top ? ["P"] : ["C0","C1","C2","C3"];
    translate([wrap_margin, y])
      wrap_segment_base(d_mm, h_mm, marks, s, nseg, aA, aL);
  }
}

// ============================================================
// MAIN
// ============================================================
page_border_portrait_centered();
translate([-page_w/2, -page_h/2])
  translate([0, base_page_w]) rotate(-90)
    wrap_single_page_base(wall_mode);
