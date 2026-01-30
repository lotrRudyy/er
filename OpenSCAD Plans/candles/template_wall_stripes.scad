// OpenSCAD 2021.01
// A4 WRAP STRIPS ONLY (baked constellation_id = 16)
//
// OUTPUT:
// - WRAP_BOTH: bottom + top wall strips, as many segments as fit on one A4.
// - Use wrap_page = 0,1,2.. if there are more segments than fit.
//
// PRINT RULE: Export as SVG (recommended), print at 100% (no fit-to-page).
// ASCII ONLY.

$fn = 96;

// ============================================================
// USER INPUT (measured build dimensions)
// ============================================================

// Which page of segments to show
wrap_page = 0;

// Disk diameters (mm)
token_bottom_d = 200;
token_top_d    = 170;

// Wall heights (mm)
token_bottom_h = 12;
token_top_h    = 12;

// Wall mark width
wall_mark_w = 0.425;

// Wrap tiling
wrap_overlap = 8;      // mm overlap field at END of each segment (right side only)
wrap_gap_y   = 8;      // vertical gap between stacked segments
wrap_margin  = 25;     // bigger margins for real printers

// Page size (A4 portrait, mm)
page_w = 210;
page_h = 297;

// Internal layout base coords (A4 landscape, mm)
base_page_w = 297;
base_page_h = 210;

// ============================================================
// BAKED DATA (constellation_id = 16)
// ============================================================

global_bottom_marks = [
  92.2362, 40.8802, 55.5225, 50.3159, 258.546, 250.841, 246.945, 129.35, 141.644, 194.483,
  333.372, 325.721, 186.199, 217.578, 210.179, 153.336, 353.209, 201.052, 8.72721, 122.153
];

global_top_marks = [
  158.958, 48.2201, 118.696, 82.0312, 212.456, 253.036, 186.202, 274.864, 13.6619, 97.5625,
  204.588, 264.389, 227.571, 287.806, 220.341, 90.7055, 243.061, 236.142, 193.614, 304.053
];

// ============================================================
// HELPERS
// ============================================================
function _deg_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function _circumference(d) = PI * d;
function _ang_to_x(ang_deg, circ_mm) = circ_mm * (_deg_norm(ang_deg) / 360);

// A4 usable strip width (inside margins)
function usable_w() = base_page_w - 2*wrap_margin;

// Compute number of segments for a circumference given usable width + overlap
function seg_step() = usable_w() - wrap_overlap;
function seg_count(circ_mm) = ceil((circ_mm - wrap_overlap) / seg_step());

// Segment window start/end in full-strip coordinates
function seg_x0(seg) = seg * seg_step();
function seg_x1(seg, circ_mm) = min(seg_x0(seg) + usable_w(), circ_mm);

// ============================================================
// 2D DRAW PRIMITIVES
// ============================================================
stroke = 0.30;
font_name = "Liberation Sans";
title_size = 5;
info_size = 3.2;

module label(txt, sz=6) {
  text(txt, size=sz, font=font_name, halign="left", valign="baseline");
}

module line2d(x0,y0,x1,y1,w=0.35) {
  dx = x1-x0;
  dy = y1-y0;
  L = sqrt(dx*dx + dy*dy);
  ang = atan2(dy, dx);
  translate([x0,y0]) rotate(ang) translate([0,-w/2]) square([max(0.01,L), w], center=false);
}

module scale_bar_50mm(x, y) {
  translate([x, y]) {
    square([50, 0.6], center=false);
    translate([0, 2.5]) label("50mm", info_size);
  }
}

// Portrait page border, centered around origin
module page_border_portrait_centered() {
  translate([-page_w/2, -page_h/2]) {
    difference() {
      square([page_w, page_h], center=false);
      translate([stroke, stroke]) square([page_w-2*stroke, page_h-2*stroke], center=false);
    }
  }
}

// Overlap field at END of a segment (right side only).
// You lay the START of the next segment on top of this overlap field.
module overlap_field_end(w, h_mm, overlap_w) {
  if (overlap_w > 0.01) {
    x0 = w - overlap_w;

    // overlap box outline
    difference() {
      translate([x0, 0]) square([overlap_w, h_mm], center=false);
      translate([x0 + stroke, stroke])
        square([max(0.01, overlap_w - 2*stroke), max(0.01, h_mm - 2*stroke)], center=false);
    }

    // placement line where the next segment START (x=0) should align
    line2d(x0, 0, x0, h_mm, 0.30);

    // simple A/B/C tick marks at far right edge
    translate([w - 2.5, h_mm/2]) {
      for (k=[-1:1]) translate([0, k*3]) square([2.0, 0.5], center=true);
      translate([4,  3]) label("A", info_size);
      translate([4,  0]) label("B", info_size);
      translate([4, -3]) label("C", info_size);
    }
  }
}

// ============================================================
// WRAP SEGMENT (BASE LANDSCAPE coordinates)
// Draw ONLY the desired wall mark lines (no extra tick marks).
// ============================================================
module wrap_segment_base(d_mm, h_mm, marks_angles, seg, segN) {
  circ = _circumference(d_mm);
  x0 = seg_x0(seg);
  x1 = seg_x1(seg, circ);
  w  = x1 - x0;

  // Segment outline
  difference() {
    square([w, h_mm], center=false);
    translate([stroke, stroke])
      square([max(0.01,w-2*stroke), max(0.01,h_mm-2*stroke)], center=false);
  }

  // Overlap field at end of segment (right side only)
  overlap_w = (seg < segN-1) ? wrap_overlap : 0;
  overlap_field_end(w, h_mm, overlap_w);

  // Marks inside this segment window
  for (a = marks_angles) {
    xm = _ang_to_x(a, circ);
    if (xm >= x0 && xm <= x1) {
      translate([xm - x0 - wall_mark_w/2, 0]) square([wall_mark_w, h_mm], center=false);
    }
  }
}

// ============================================================
// WRAP PAGE (BASE LANDSCAPE coords): bottom + top on one sheet
// ============================================================
module wrap_both_page_base() {
  bottom_circ = _circumference(token_bottom_d);
  top_circ    = _circumference(token_top_d);

  nb = seg_count(bottom_circ);
  nt = seg_count(top_circ);

  hx = wrap_margin;
  lh = 4.2;

  // 50mm bar on top-right
  scale_bar_50mm(base_page_w - wrap_margin - 70, base_page_h - wrap_margin);

  // Header (BOTTOM)
  bottom_head_y = base_page_h - wrap_margin;
  translate([hx, bottom_head_y])        label("WRAP STRIP (BOTTOM WALL)", title_size);
  translate([hx, bottom_head_y - lh])   label(str("D=", token_bottom_d, " circ.=", bottom_circ, " wall_h=", token_bottom_h, " seg=", nb, " overlap=", wrap_overlap), info_size);

  // bottom segments per page
  bottom_header_h = 2*lh + 10;
  bottom_avail_h  = (base_page_h - wrap_margin) - (wrap_margin) - bottom_header_h;
  per_page_b = max(1, floor(bottom_avail_h / (token_bottom_h + wrap_gap_y)));

  x_seg = wrap_margin;
  y_start_bottom = bottom_head_y - bottom_header_h - 5;

  for (s=[0:nb-1]) {
    page_of_s = floor(s / per_page_b);
    if (page_of_s == wrap_page) {
      slot = s - wrap_page*per_page_b;
      y_s = y_start_bottom - slot*(token_bottom_h + wrap_gap_y);
      translate([x_seg, y_s]) wrap_segment_base(token_bottom_d, token_bottom_h, global_bottom_marks, s, nb);
      translate([x_seg, y_s + token_bottom_h + 2]) label(str("seg ", s), info_size);
    }
  }

  // TOP header close below the last bottom segment shown on this page
  bottom_shown = min(per_page_b, nb - wrap_page*per_page_b);
  y_after_bottom = y_start_bottom - bottom_shown*(token_bottom_h + wrap_gap_y) + 8;

  top_head_y = y_after_bottom;
  translate([hx, top_head_y])           label("WRAP STRIP (TOP WALL)", title_size);
  translate([hx, top_head_y - lh])      label(str("D=", token_top_d, " circ.=", top_circ, " wall_h=", token_top_h, " seg=", nt, " overlap=", wrap_overlap), info_size);

  // top segments per page
  top_header_h = 2*lh + 10;
  top_avail_h  = (top_head_y - top_header_h) - wrap_margin;
  per_page_t = max(1, floor(top_avail_h / (token_top_h + wrap_gap_y)));

  y_start_top = top_head_y - top_header_h - 10;

  for (s=[0:nt-1]) {
    page_of_s = floor(s / per_page_t);
    if (page_of_s == wrap_page) {
      slot = s - wrap_page*per_page_t;
      y_s = y_start_top - slot*(token_top_h + wrap_gap_y);
      translate([x_seg, y_s]) wrap_segment_base(token_top_d, token_top_h, global_top_marks, s, nt);
      translate([x_seg, y_s + token_top_h + 2]) label(str("seg ", s), info_size);
    }
  }
}

// ============================================================
// ROTATION / CENTERING WRAPPER
// We layout in BASE landscape [0..base_page_w]x[0..base_page_h].
// Then rotate RIGHT into portrait and center the portrait page around origin.
// Mapping (clockwise): (x,y) -> (y, base_page_w - x)
// Implemented as: translate([0, base_page_w]) rotate(-90) <base>
// ============================================================
module draw_portrait_centered_from_base() {
  page_border_portrait_centered();

  // Draw base content, rotated into portrait, then centered
  translate([-page_w/2, -page_h/2]) {
    translate([0, base_page_w]) rotate(-90) children();
  }
}

// ============================================================
// MAIN
// ============================================================
draw_portrait_centered_from_base() wrap_both_page_base();

