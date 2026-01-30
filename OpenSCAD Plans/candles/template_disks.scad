// OpenSCAD 2021.01
// A4 PRINT TEMPLATES (baked constellation_id = 16)
//
// output_mode = 0  -> NAILS (one A4 page per board hole; nails_page = 0..3)
//
// PRINT RULE: Export as SVG/PDF, print at 100% (NO fit-to-page).
// ASCII ONLY.

$fn = 96;

// ============================================================
// USER INPUT
// ============================================================

nails_page  = 3;     // 0..3 (which hole/disk page)

// Measured build dimensions (mm)
token_bottom_d = 200;
token_top_d    = 170;
hole_d         = 7.2;

// Bottom disk step (same as main build)
bottom_steps_total = 18;
bottom_disk_step   = 0;     // 0..17

// Nail radius from hole center (mm): r = (token_bottom_d/2) + nail_offset_from_disk
nail_offset_from_disk = -30;  // your current setting (can be negative)

// Bridge holes (two holes, at the zone edges, ~2cm away from disk)
bridge_offset_from_disk = 20; // mm outward from bottom disk radius
bridge_hole_d = 6.0;          // mm (drill)

// Candle markers on annulus (for reference / small-candle placement)
candle_hole_d = 4.0;          // mm (visual + drill size if needed)
candle_label_size = 7;        // mm text size
candle_label_radial_offset = 9; // mm outward from candle hole

// Printer safe margin (keep all geometry inside this margin)
printer_margin = 12;          // mm

// Page size (A4 landscape) in mm
page_w = 297;
page_h = 210;

// 2D stroke thickness for "lines" (rectangles)
stroke = 0.35;
font_name = "Liberation Sans";

// ============================================================
// BAKED DATA (constellation_id = 16)
// ============================================================

// Small-candle base angles (labels 0..3) BEFORE bottom-rotation alignment.
baked_candle_angles = [334.425, 246.379, 130.303, 54.2855];

// Zone centers (one owned by each disk: 0=red,1=green,2=blue,3=yellow)
baked_zone_center_ang = [83.9371, 49.5987, 292.977, 252.763];
zone_halfspan_deg = 17;

// Nail world angles per hole (i=0..3)
baked_nail_ang = [139.746, 50.1615, 290.612, 208.639];

// Big candle height mapping (disk i sits under this level)
levels = [0, 2, 3, 1];

// ============================================================
// HELPERS
// ============================================================

function _deg_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function _step_deg() = 360 / bottom_steps_total;

// Frame rotation: rotate so candle "levels[i]" points to +Y (90deg), then apply step.
function _bottom_rot_total(i) =
  (90 - baked_candle_angles[levels[i]]) + bottom_disk_step * _step_deg();

// Candle world angle (label p on disk i) after bottom alignment+step
function candle_world_ang(i, p) =
  _deg_norm(baked_candle_angles[p] + _bottom_rot_total(i));

// Zone angles (baked centers are disk-local -> convert to world by adding bottom rotation)
function zone_center_world(i) = _deg_norm(baked_zone_center_ang[i] + _bottom_rot_total(i));
function zone_a0(i) = _deg_norm(zone_center_world(i) - zone_halfspan_deg);
function zone_a1(i) = _deg_norm(zone_center_world(i) + zone_halfspan_deg);

// ============================================================
// 2D PRIMITIVES (NO filled big shapes)
// ============================================================

module page_border() {
  // Outline-only border centered at origin
  translate([-page_w/2, -page_h/2]) {
    difference() {
      square([page_w, page_h], center=false);
      translate([stroke, stroke]) square([page_w-2*stroke, page_h-2*stroke], center=false);
    }
  }
}

module label(txt, sz=5, hal="left", val="baseline") {
  text(txt, size=sz, font=font_name, halign=hal, valign=val);
}

// line as thin rectangle
module line2d(x0,y0,x1,y1,w=0.35) {
  dx = x1-x0;
  dy = y1-y0;
  L = sqrt(dx*dx + dy*dy);
  ang = atan2(dy, dx);
  translate([x0,y0]) rotate(ang) translate([0,-w/2]) square([max(0.01,L), w], center=false);
}

// outline circles to avoid filled blobs
module circle_outline_d(d, w=0.35) {
  difference() {
    circle(d=d);
    circle(d=max(0.01, d - 2*w));
  }
}

module crosshair(size=12, w=0.35) {
  translate([-w/2, -size/2]) square([w, size], center=false);
  translate([-size/2, -w/2]) square([size, w], center=false);
}

// ============================================================
// NAILS PAGE (one page per hole i)
// For hole 3 ONLY: rotate the entire content by +90deg about origin.
// ============================================================

module nails_page(i) {
  page_border();

  module content() {
    // Common center at origin
    cx = 0;
    cy = 0;

    // Radii
    r_bot    = token_bottom_d/2;
    r_top    = token_top_d/2;
    r_nail   = r_bot + nail_offset_from_disk;
    r_bridge = r_bot + bridge_offset_from_disk;

    // Candle ring radius (middle of annulus)
    ann_mid = (r_bot + r_top)/2;
    r_candle = ann_mid;

    // Draw disk outlines
    translate([cx, cy]) circle_outline_d(token_bottom_d, stroke);
    translate([cx, cy]) circle_outline_d(token_top_d, stroke);

    // Hole center mark
    translate([cx, cy]) {
      crosshair(16, stroke);
      circle_outline_d(hole_d, stroke);
    }

    // TOP DISK LINE: direction = nail angle in world space
    aN = baked_nail_ang[i];
    line2d(cx, cy, cx + r_top*cos(aN), cy + r_top*sin(aN), stroke*1.4);

    // NAIL HOLE + LABEL
    nx = cx + r_nail*cos(aN);
    ny = cy + r_nail*sin(aN);
    translate([nx, ny]) circle_outline_d(2.2, stroke); // nail drill mark

    // label "N{i}" (shifted outwards + slight tangential offset)
    lx = nx + 10*cos(aN) - 4*sin(aN);
    ly = ny + 10*sin(aN) + 4*cos(aN);
    translate([lx, ly]) label(str("N", i), 6, "center", "center");

    // BRIDGE HOLES: exactly at zone edges (world)
    a0 = zone_a0(i);
    a1 = zone_a1(i);

    bx0 = cx + r_bridge*cos(a0);
    by0 = cy + r_bridge*sin(a0);
    bx1 = cx + r_bridge*cos(a1);
    by1 = cy + r_bridge*sin(a1);

    translate([bx0, by0]) circle_outline_d(bridge_hole_d, stroke);
    translate([bx1, by1]) circle_outline_d(bridge_hole_d, stroke);

    // Guide lines (optional)
    line2d(cx, cy, bx0, by0, stroke*0.8);
    line2d(cx, cy, bx1, by1, stroke*0.8);

    // CANDLES: C0..C3
    for (p=[0:3]) {
      aa = candle_world_ang(i, p);
      px = cx + r_candle*cos(aa);
      py = cy + r_candle*sin(aa);

      translate([px, py]) circle_outline_d(candle_hole_d, stroke);

      tx = cx + (r_candle + candle_label_radial_offset)*cos(aa);
      ty = cy + (r_candle + candle_label_radial_offset)*sin(aa);
      translate([tx, ty]) label(str("C", p), candle_label_size, "center", "center");
    }

    // small page id
    translate([-page_w/2 + printer_margin + 2, -page_h/2 + printer_margin + 2])
      label(str("hole ", i), 4, "left", "baseline");
  }

  // Only hole 3 gets rotated by +90deg around origin
  if (i == 3) rotate(90) content();
  else        content();
}

// ============================================================

// ============================================================
// MAIN
// ============================================================
rotate([0, 0, 90]) {
    nails_page(nails_page);
}
