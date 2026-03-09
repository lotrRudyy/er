// OpenSCAD 2021.01
// A4 PRINT TEMPLATES (SYNCED FROM candles.scad; constellation_id = 16)
//
// OUTPUT:
// - page_id = 0..3 selects which board hole/disk template page to render.
//
// PRINT RULE: Export as SVG/PDF, print at 100% (NO fit-to-page).
// ASCII ONLY.

$fn = 96;

// ============================================================
// USER INPUT
// ============================================================

page_id = 3;        // 0..3 which hole/disk page

// Page size (A4 landscape) in mm
page_w = 297;
page_h = 210;

// Printer safe margin (keep all geometry inside this margin)
printer_margin = 12;

// 2D stroke thickness for helper lines (rectangles)
stroke = 0.35;
font_name = "Liberation Sans";

// ============================================================
// SYNCED DIMENSIONS (from candles.scad; converted to mm)
// ============================================================

// Main disk diameters (mm)
token_bottom_d = 184;
token_top_d    = 140;

// Board hole diameter (mm)
hole_d         = 12;

// Disk heights (mm) (not used for 2D, kept for reference)
token_bottom_h = 23;
token_top_h    = 23;

// Bottom disk step (same as main build)
bottom_steps_total = 18;
bottom_disk_step   = 0;   // 0..17

// Zone half-span (deg)
zone_halfspan_deg  = 17;

// Nail drill mark diameter (mm) (from candles.scad nail_r)
nail_drill_d = 6;

// Bridge drill mark diameter (mm) (template-only visual marker)
bridge_hole_d = 15;

// Candle drill/visual diameter (mm)
candle_hole_d = 4.0;
candle_label_size = 7;
candle_label_radial_offset = 9;

// ============================================================
// SYNCED FIXED ANGLES / POSITIONS (from candles.scad)
// All angles are WORLD angles around each hole center.
// Distances are from that hole center, in mm.
// ============================================================

// Annulus candle base angles (label==index) in DISK-LOCAL coords (deg)
// (these rotate with the bottom disk)
candle_angles = [272, 49, 126, 329];

// Big candle height mapping (disk i sits under this level)
levels = [0, 2, 3, 1];

// nail_polar[i] = [angle_deg, distance_mm] (WORLD angle, distance from disk center)
nail_polar = [
  [220.000000, 11.050000],
  [120.000000, 15.050000],
  [220.000000, 13.050000],
  [50.000000, 14.050000]
];

// bridge_polar[i] = [center_angle_deg, distance_mm]
// Zone center is aligned to this WORLD angle.
bridge_polar = [
  [150, 105.5],
  [340, 105.5],
  [185, 105.5],
  [185, 105.5]
];

// ============================================================
// HELPERS
// ============================================================

function _deg_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function _step_deg() = 360 / bottom_steps_total;

function candle_ang(label) = candle_angles[label];

// Frame rotation: rotate so candle "levels[i]" points to +Y (90deg), then apply step.
function frame_rot_for_disk(i) =
  let(A = candle_ang(levels[i]))
  (90 - A);

bottom_rot_deg = bottom_disk_step * _step_deg();

// Total bottom rotation in WORLD (deg)
function world_rot_bottom(i) = _deg_norm(frame_rot_for_disk(i) + bottom_rot_deg);

// Candle world angle (label p on disk i) after bottom alignment+step
function candle_world_ang(i, p) = _deg_norm(candle_ang(p) + world_rot_bottom(i));

// Nail polar
function nail_ang(i)  = nail_polar[i][0];
function nail_dist(i) = nail_polar[i][1];

// Bridge polar
function bridge_ang(i)  = bridge_polar[i][0];
function bridge_dist(i) = bridge_polar[i][1];

// Zone edges in WORLD (deg) around hole i
function zone_a0_world(i) = _deg_norm(bridge_ang(i) - zone_halfspan_deg);
function zone_a1_world(i) = _deg_norm(bridge_ang(i) + zone_halfspan_deg);

// ============================================================
// 2D PRIMITIVES
// ============================================================

module page_border() {
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

module line2d(x0,y0,x1,y1,w=0.35) {
  dx = x1-x0;
  dy = y1-y0;
  L = sqrt(dx*dx + dy*dy);
  ang = atan2(dy, dx);
  translate([x0,y0]) rotate(ang) translate([0,-w/2]) square([max(0.01,L), w], center=false);
}

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
// TEMPLATE PAGE (one page per hole i)
// ============================================================

module template_page(i) {
  page_border();

  // Common center at origin
  cx = 0;
  cy = 0;

  r_bot = token_bottom_d/2;
  r_top = token_top_d/2;

  // Candle ring radius = mid of annulus
  r_candle = (r_bot + r_top)/2;

  // Disk outlines
  translate([cx, cy]) circle_outline_d(token_bottom_d, stroke);
  translate([cx, cy]) circle_outline_d(token_top_d, stroke);

  // Board hole center mark
  translate([cx, cy]) {
    crosshair(16, stroke);
    circle_outline_d(hole_d, stroke);
  }

  // TOP DISK LINE: direction = nail world angle (this is where the top pointer aligns)
  aN = nail_ang(i);
  line2d(cx, cy, cx + r_top*cos(aN), cy + r_top*sin(aN), stroke*1.4);

  // NAIL MARK (drill circle at nail_dist)
  nx = cx + nail_dist(i)*cos(aN);
  ny = cy + nail_dist(i)*sin(aN);
  translate([nx, ny]) circle_outline_d(nail_drill_d, stroke);

  // label "N<i>"
  lx = nx + 10*cos(aN) - 4*sin(aN);
  ly = ny + 10*sin(aN) + 4*cos(aN);
  translate([lx, ly]) label(str("N", i), 6, "center", "center");

  // BRIDGE EDGE HOLES (at zone edges on radius bridge_dist)
  a0 = zone_a0_world(i);
  a1 = zone_a1_world(i);

  bx0 = cx + bridge_dist(i)*cos(a0);
  by0 = cy + bridge_dist(i)*sin(a0);
  bx1 = cx + bridge_dist(i)*cos(a1);
  by1 = cy + bridge_dist(i)*sin(a1);

  translate([bx0, by0]) circle_outline_d(bridge_hole_d, stroke);
  translate([bx1, by1]) circle_outline_d(bridge_hole_d, stroke);

  // Guide rays (thin)
  line2d(cx, cy, bx0, by0, stroke*0.8);
  line2d(cx, cy, bx1, by1, stroke*0.8);

  // CANDLES: C0..C3 (world after bottom rotation)
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
    label(str("hole ", i), 5, "left", "baseline");
  
  // 50mm scale bar anchored to the actual printed page (portrait, centered coords)
  translate([page_w/2 + printer_margin + 2 - 80, -page_h/2 + printer_margin + 2]) square([50, 0.8], center=false);
  translate([page_w/2 + printer_margin + 2 - 80, -page_h/2 + printer_margin + 7]) label("50mm", 5);
}


// ============================================================
// MAIN
// ============================================================

rotate([0,0,90]) template_page(page_id);
