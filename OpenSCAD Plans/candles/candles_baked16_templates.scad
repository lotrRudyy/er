// OpenSCAD 2021.01
// A4 PRINT TEMPLATES (baked constellation_id = 16)
//
// output_mode = 0  -> NAILS (all 4 on one page; overlay on one common HOLE CENTER)
// output_mode = 1  -> WRAP_BOTTOM (as many segments as fit on one A4)
// output_mode = 2  -> WRAP_TOP (as many segments as fit on one A4)
//
// PRINT RULE: Export as SVG, print at 100% (no fit-to-page).
// ASCII ONLY.

$fn = 96;

// ============================================================
// USER INPUT (measured build dimensions)
// ============================================================

// output_mode: 0=nails, 1=wrap_bottom, 2=wrap_top
output_mode = 0;

// wrap_page: 0,1,2.. if segments don't fit on one sheet
wrap_page = 0;

// Disk diameters (mm)
token_bottom_d = 181;
token_top_d    = 142;

// Wall heights (mm)
token_bottom_h = 23;
token_top_h    = 23;

// Nail offset from bottom disk radius (mm)
nail_offset_from_disk = -30;

// Hole diameter (mm)
hole_d = 7.2;

// Wall mark width (HALVED)
wall_mark_w = 0.425;

// Wrap tiling
wrap_overlap = 8;          // mm overlap between adjacent segments (also for LAST->FIRST closure)
wrap_gap_y   = 8;          // vertical gap between stacked segments on page (more space)
wrap_margin  = 25;         // page margin (bigger for real printers)

// Page size (A4 landscape, mm) FOR BASE LAYOUT
base_page_w = 297;
base_page_h = 210;

// Final page is portrait after rotate-right
page_w = 210;
page_h = 297;

// ============================================================
// BAKED DATA (constellation_id = 16)
// ============================================================

// Nails (world angles) – used to place nail points around common center
baked_nail_ang = [139.746, 50.1615, 290.612, 208.639];

// Small candle icon angles (labels 0..3) – baked
baked_candle_angles = [334.425, 246.379, 130.303, 54.2855];

// Zone centers (owned by disk0..3 => R,G,B,Y) – baked
baked_box_local_angles = [83.9371, 49.5987, 292.977, 252.763];

// Zone half span (deg)
zone_halfspan_deg = 17;

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
// 2D DRAW PRIMITIVES (stroke-like with thin rectangles)
// ============================================================
stroke = 0.30;
font_name = "Liberation Sans";
title_size = 4;
info_size = 3;

module page_border_portrait_centered() {
  translate([-page_w/2, -page_h/2])
    difference() {
      square([page_w, page_h], center=false);
      translate([stroke, stroke]) square([page_w-2*stroke, page_h-2*stroke], center=false);
    }
}

module page_border_base_landscape() {
  difference() {
    square([base_page_w, base_page_h], center=false);
    translate([stroke, stroke]) square([base_page_w-2*stroke, base_page_h-2*stroke], center=false);
  }
}

module crosshair(size=10, w=0.25) {
  translate([-w/2, -size/2]) square([w, size], center=false);
  translate([-size/2, -w/2]) square([size, w], center=false);
}

module label(txt, sz=6) {
  text(txt, size=sz, font=font_name, halign="center", valign="center");
}

// Thin 2D line as a rectangle
module line2d(x0,y0,x1,y1,w=0.35) {
  dx = x1-x0;
  dy = y1-y0;
  L = sqrt(dx*dx + dy*dy);
  ang = atan2(dy, dx);
  translate([x0,y0]) rotate(ang) translate([0,-w/2]) square([max(0.01,L), w], center=false);
}

// ============================================================
// Overlap field at END of a segment (right side only).
// You lay the START of the next segment on top of this overlap field.
// Includes A/B/C marks near far right edge.
// ============================================================
module overlap_field_end(w, h_mm, overlap_w) {
  if (overlap_w > 0.01) {
    x0 = w - overlap_w;

    difference() {
      translate([x0, 0]) square([overlap_w, h_mm], center=false);
      translate([x0 + stroke, stroke])
        square([max(0.01, overlap_w - 2*stroke), max(0.01, h_mm - 2*stroke)], center=false);
    }

    line2d(x0, 0, x0, h_mm, 0.30);

    translate([w - 2.5, h_mm/2]) {
      for (k=[-1:1]) translate([0, k*3]) square([2.0, 0.5], center=true);
      translate([4,  3]) text("A", size=info_size, font=font_name, halign="left", valign="center");
      translate([4,  0]) text("B", size=info_size, font=font_name, halign="left", valign="center");
      translate([4, -3]) text("C", size=info_size, font=font_name, halign="left", valign="center");
    }
  }
}

// ============================================================
// NAILS PAGE (BASE LANDSCAPE): ALL 4 on ONE page
// + zone outlines + color names inside zones + small candle markers
// ============================================================
module nails_all_page_base() {
  page_border_base_landscape();

  cx = base_page_w/2;
  cy = base_page_h/2;

  translate([cx, cy]) difference() {
    circle(d=token_bottom_d);
    circle(d=max(0.01, token_bottom_d - 2*stroke));
  }

  translate([cx, cy]) {
    crosshair(20, 0.28);
    circle(d=hole_d);
    translate([28, 0]) text("HOLE CENTER", size=info_size, font=font_name, halign="left", valign="center");
  }

  rr = token_bottom_d/2 + nail_offset_from_disk;

  for (i=[0:3]) {
    a = baked_nail_ang[i];
    nx = cx + rr * cos(a);
    ny = cy + rr * sin(a);

    line2d(cx, cy, nx, ny, 0.30);

    ah = 6;
    at = 2.6;
    ang = atan2(ny-cy, nx-cx);
    translate([nx, ny]) rotate(ang) polygon(points=[[0,0],[-ah, at],[-ah,-at]]);

    translate([nx, ny]) {
      crosshair(14, 0.26);
      circle(d=2.2);
    }

    lx = nx + 8*cos(a);
    ly = ny + 8*sin(a);
    translate([lx, ly]) text(str("hole ", i), size=info_size, font=font_name, halign="center", valign="center");
  }

  zone_names  = ["RED", "GREEN", "BLUE", "YELLOW"];
  zone_r_line = token_bottom_d/2 * 0.96;

  label_r = token_bottom_d/2 * 0.62;
  label_t = 10;

  for (k=[0:3]) {
    cang = baked_box_local_angles[k];
    a0 = cang - zone_halfspan_deg;
    a1 = cang + zone_halfspan_deg;

    x0 = cx + zone_r_line * cos(a0);
    y0 = cy + zone_r_line * sin(a0);
    x1 = cx + zone_r_line * cos(a1);
    y1 = cy + zone_r_line * sin(a1);

    line2d(cx, cy, x0, y0, 0.28);
    line2d(cx, cy, x1, y1, 0.28);

    tang_sgn = (k % 2 == 0) ? 1 : -1;
    lx = cx + label_r * cos(cang) + tang_sgn * (-sin(cang)) * label_t;
    ly = cy + label_r * sin(cang) + tang_sgn * ( cos(cang)) * label_t;

    translate([lx, ly]) text(zone_names[k], size=info_size, font=font_name, halign="center", valign="center");
  }

  candle_r = (token_top_d/2 + token_bottom_d/2) / 2;

  for (p=[0:3]) {
    a = baked_candle_angles[p];
    px = cx + candle_r * cos(a);
    py = cy + candle_r * sin(a);

    translate([px, py]) {
      difference() {
        circle(d=4.0);
        circle(d=max(0.01, 4.0 - 2*stroke));
      }
      translate([0, -6]) text(str(p), size=info_size, font=font_name, halign="center", valign="center");
    }
  }
}

// ============================================================
// WRAP SEGMENT DRAWER (BASE LANDSCAPE)
// NOTE: overlap field is now ALSO drawn on the LAST segment,
// because it overlaps back onto segment 0 to close the loop.
// ============================================================
module wrap_segment_base(d_mm, h_mm, marks_angles, seg, segN) {
  circ = _circumference(d_mm);
  x0 = seg_x0(seg);
  x1 = seg_x1(seg, circ);
  w  = x1 - x0;

  difference() {
    square([w, h_mm], center=false);
    translate([stroke, stroke]) square([max(0.01,w-2*stroke), max(0.01,h_mm-2*stroke)], center=false);
  }

  // ALWAYS draw overlap field (including last segment -> wraps onto seg0)
  overlap_field_end(w, h_mm, wrap_overlap);

  for (a = marks_angles) {
    xm = _ang_to_x(a, circ);
    if (xm >= x0 && xm <= x1) {
      translate([xm - x0 - wall_mark_w/2, 0]) square([wall_mark_w, h_mm], center=false);
    }
  }
}

// ============================================================
// WRAP BOTTOM PAGE (BASE LANDSCAPE)
// ============================================================
module wrap_bottom_page_base() {
  page_border_base_landscape();

  circ = _circumference(token_bottom_d);
  nseg = seg_count(circ);

  hx = wrap_margin;
  lh = 4.2;

  head_y = base_page_h - wrap_margin;
  translate([hx, head_y])      text("WRAP STRIP (BOTTOM WALL)", size=title_size, font=font_name, halign="left", valign="baseline");
  translate([hx, head_y-lh])   text(str("D=", token_bottom_d, " circ.=", circ, " wall_h=", token_bottom_h, " seg=", nseg, " overlap=", wrap_overlap),
                                   size=info_size, font=font_name, halign="left", valign="baseline");

  header_h = 2*lh + 10;
  avail_h  = (base_page_h - wrap_margin) - (wrap_margin) - header_h;
  per_page = max(1, floor(avail_h / (token_bottom_h + wrap_gap_y)));

  x_seg = wrap_margin;
  y0 = head_y - header_h - 20;

  for (s=[0:nseg-1]) {
    if (floor(s/per_page) == wrap_page) {
      slot = s - wrap_page*per_page;
      y = y0 - slot*(token_bottom_h + wrap_gap_y);
      translate([x_seg, y]) wrap_segment_base(token_bottom_d, token_bottom_h, global_bottom_marks, s, nseg);
      translate([x_seg, y + token_bottom_h + 2]) text(str("seg ", s), size=info_size, font=font_name, halign="left", valign="baseline");
    }
  }
}

// ============================================================
// WRAP TOP PAGE (BASE LANDSCAPE)
// ============================================================
module wrap_top_page_base() {
  page_border_base_landscape();

  circ = _circumference(token_top_d);
  nseg = seg_count(circ);

  hx = wrap_margin;
  lh = 4.2;

  head_y = base_page_h - wrap_margin;
  translate([hx, head_y])      text("WRAP STRIP (TOP WALL)", size=title_size, font=font_name, halign="left", valign="baseline");
  translate([hx, head_y-lh])   text(str("D=", token_top_d, " circ.=", circ, " wall_h=", token_top_h, " seg=", nseg, " overlap=", wrap_overlap),
                                   size=info_size, font=font_name, halign="left", valign="baseline");

  header_h = 2*lh + 10;
  avail_h  = (base_page_h - wrap_margin) - (wrap_margin) - header_h;
  per_page = max(1, floor(avail_h / (token_top_h + wrap_gap_y)));

  x_seg = wrap_margin;
  y0 = head_y - header_h - 20;

  for (s=[0:nseg-1]) {
    if (floor(s/per_page) == wrap_page) {
      slot = s - wrap_page*per_page;
      y = y0 - slot*(token_top_h + wrap_gap_y);
      translate([x_seg, y]) wrap_segment_base(token_top_d, token_top_h, global_top_marks, s, nseg);
      translate([x_seg, y + token_top_h + 2]) text(str("seg ", s), size=info_size, font=font_name, halign="left", valign="baseline");
    }
  }
}

// ============================================================
// FINAL: rotate everything 90deg RIGHT and center page around origin
// ============================================================
module draw_final_centered_rot_right() {
  page_border_portrait_centered();

  translate([-page_w/2, -page_h/2]) {
    multmatrix([
      [ 0, 1, 0, 0           ],
      [-1, 0, 0, base_page_w ],
      [ 0, 0, 1, 0           ],
      [ 0, 0, 0, 1           ]
    ]) {
      if (output_mode == 0) {
        nails_all_page_base();
      } else if (output_mode == 1) {
        wrap_bottom_page_base();
      } else {
        wrap_top_page_base();
      }
    }
  }
}

// ============================================================
// MAIN
// ============================================================
draw_final_centered_rot_right();
