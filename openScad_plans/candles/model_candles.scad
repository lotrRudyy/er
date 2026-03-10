// OpenSCAD 2021.01
// Candles + 4 holes + 4 disks
//
// CURRENT MODEL GUARANTEES:
// - Deterministic RNG: seed_* + constellation_id
// - Zones: generated sequentially with retry and MUST NOT TOUCH (min sep >= 2*zone_halfspan_deg)
// - Rotation order: align bottom (frame+step), align top (to nail), THEN zones
// - Top zones match bottom zones in WORLD space (do not follow black line)
// - ONE physical token disk: wall marks are IDENTICAL for all 4 placements
//
// RIDDLE WALL LINES GOAL:
// - SAME physical disk => SAME wall marks for all placements
// - For each hole i, after aligning bottom+top per that hole, the TOTAL number of wall marks
//   (bottom-wall + top-wall) inside the CORRESPONDING COLOR ZONE equals zone_solution_counts[i].
// - Unambiguous: at least half the line thickness is clearly inside/outside (+ extra clearance).
//
// FULL FILE. ASCII ONLY.

$fn = 96;

// ============================================================
// USER PARAMETERS
// ============================================================
show_board = true;
show_disks = true;
show_nails = true;

bottom_steps_total = 18;
bottom_disk_step   = 0;      // 0..17

constellation_id   = 16;

// Top-disk pointer angle in disk LOCAL frame (FIXED, no RNG)
pointer_ang_local_deg = 90.0;  // degrees; 90 means +Y



// wall "line marks" on disk side walls (top + bottom).
// IMPORTANT: there is ONE physical token disk -> marks are IDENTICAL for all 4 rendered placements.

wall_mark_w   = 0.425;         // tangential width (HALVED)
wall_mark_out = 0.55;          // radial protrusion outward from cylinder wall
wall_mark_z_inset = 0.12;      // keep marks away from wall edges

// Clear counting rules:
line_in_edge_clear_deg     = 6.0;   // buffer (deg) beyond half thickness for marks that COUNT inside a zone
line_outside_clear_deg     = 12.0;  // buffer beyond half thickness for marks that must be OUTSIDE a zone

// Required per hole i (L->R): inside its OWN color zone (red/green/blue/yellow),
// the TOTAL number of marks on BOTH walls (bottom+top walls) inside that zone equals:
zone_solution_counts = [2,4,1,3];

// zones
zone_halfspan_deg = 17;      // half-angle of each zone cone
zone_min_sep_deg  = 2*zone_halfspan_deg + 0.01; // zones cannot touch

// ============================================================
// LAYOUT / BOARD
// ============================================================
candle_spacing = 40;
front_offset_y = 44;

base_th        = 14;
base_margin_x  = 34;
base_margin_y_front = 34;
base_margin_y_back  = 80;

candle_d       = 16;
candle_h_min   = 24;
candle_h_step  = 14;

hole_d         = 7.2;
hole_clear     = 0.4;
hole_depth     = base_th + 0.6;

token_bottom_d   = 18.4;
token_bottom_h   = 2.3;

token_top_d      = 14;
token_top_h      = 2.3;

// annulus candles (2D icons)
mark_th        = 0.25;
mark_z_eps     = 0.06;

ann_body_w       = 0.5;
ann_body_h0      = 0.55;
ann_body_h_step  = 0.28;

ann_flame_w    = 0.25;
ann_flame_h    = 0.5;
ann_flame_y_gap = 0;

font_name = "Liberation Sans:style=Bold";
show_annulus_numbers = false;
ann_num_size  = 0.4;
ann_num_th    = 0.22;

annulus_auto_scale = false;     // auto-shrink annulus icons to fit the ring band
annulus_margin     = 0.00;     // mm safety gap from BOTH ring edges (outer + inner)
annulus_scale_manual = 1.00;   // used if annulus_auto_scale = false (or as an upper cap if true)

show_big_numbers = true;
big_num_size = 8.5;
big_num_th   = 0.9;
big_num_side_inset = 0.8;

// big candle height levels (L->R)
levels = [0, 2, 3, 1];

// nails
nail_r = 0.3;
nail_h         = 7.0;
nail_head_r = 0.6;
nail_head_h    = 1.2;
nail_offset_from_disk = 5;

eps = 0.05;

// ============================================================
// COLORS
// ============================================================
zone_colors = [
  [1,0,0],   // red   from disk 0
  [0,1,0],   // green from disk 1
  [0,0,1],   // blue  from disk 2
  [1,1,0]    // yellow from disk 3
];

gray_disk = [0.75,0.75,0.78];

// ============================================================
// MATH HELPERS
// ============================================================
function _deg_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function _ang_dist_deg(a,b) = let(d = abs(_deg_norm(a - b))) (d > 180 ? 360 - d : d);
function _ang_diff(a,b) =
  let(d = abs(_deg_norm(a) - _deg_norm(b)))
  (d > 180 ? 360 - d : d);

function _frac(x) = x - floor(x);
function _prand01(seed, idx) = _frac(abs(sin(seed*12.9898 + idx*78.233)) * 43758.5453);
function _rand_range(a, b, seed, idx) = a + (b-a) * _prand01(seed, idx);

function _clamp(x, a, b) = (x < a) ? a : ((x > b) ? b : x);
function _all_true(v) = (len(v) == 0) ? true : (min([for (x=v) (x ? 1 : 0)]) == 1);

function level_to_h(lvl) = candle_h_min + lvl * candle_h_step;

function base_w() = (3*candle_spacing) + 2*base_margin_x;
function base_h() = base_margin_y_back + base_margin_y_front + front_offset_y;

function x_of_i(i) = (i - 1.5) * candle_spacing;
function candle_y() = 0;
function hole_y()   = -front_offset_y;

// ============================================================
// SEQUENTIAL RANDOM ANGLES WITH RETRY (for candles + zones)
// ============================================================
function _pick_angle(seed, base_idx, attempt) =
  _deg_norm(_rand_range(0, 360, seed, base_idx + attempt));

function _ok_against(a, b, minsep) = (_ang_diff(a,b) >= minsep);

function _find_angle_1(seed, base_idx, a0, minsep, attempt=0) =
  (attempt > 500) ? _deg_norm(a0 + minsep) :
  let(a = _pick_angle(seed, base_idx, attempt))
  (_ok_against(a, a0, minsep) ? a : _find_angle_1(seed, base_idx, a0, minsep, attempt+1));

function _find_angle_2(seed, base_idx, a0, a1, minsep, attempt=0) =
  (attempt > 800) ? _deg_norm(a1 + minsep) :
  let(a = _pick_angle(seed, base_idx, attempt))
  ((_ok_against(a,a0,minsep) && _ok_against(a,a1,minsep)) ? a : _find_angle_2(seed, base_idx, a0, a1, minsep, attempt+1));

function _find_angle_3(seed, base_idx, a0, a1, a2, minsep, attempt=0) =
  (attempt > 1200) ? _deg_norm(a2 + minsep) :
  let(a = _pick_angle(seed, base_idx, attempt))
  ((_ok_against(a,a0,minsep) && _ok_against(a,a1,minsep) && _ok_against(a,a2,minsep)) ? a : _find_angle_3(seed, base_idx, a0, a1, a2, minsep, attempt+1));

function gen4_spaced_angles_seq(seed, id, minsep) =
  let(
    base = 10000 + id*1000,
    a0 = _pick_angle(seed, base+0,   0),
    a1 = _find_angle_1(seed, base+100, a0, minsep),
    a2 = _find_angle_2(seed, base+200, a0, a1, minsep),
    a3 = _find_angle_3(seed, base+300, a0, a1, a2, minsep)
  )
  [a0,a1,a2,a3];

// Candle angles (label==index)
candle_angles = [272.000000,
    49.000000, 126.000000,  329.000000]; // FIXED (no RNG)
function candle_ang(label) = candle_angles[label];

// Zones (master local angles, non-touching)
bridge_polar = [
  [150.000000, 10.550000],
  [340.000000, 10.550000],
  [185.000000, 10.550000],
  [185.000000, 10.550000]
];
function bridge_ang(i) = bridge_polar[i][0];
function bridge_dist(i) = bridge_polar[i][1];

box_local_angles = [bridge_ang(0), bridge_ang(1), bridge_ang(2), bridge_ang(3)];

// IMPORTANT: bridge_ang(i) is defined as a WORLD angle on the BOARD (around each hole).
// The disk itself is rotated by world_rot_bottom(i). Therefore the corresponding LOCAL
// zone angle on the disk must subtract that rotation.


// World rotation of each bottom disk (same as original logic)
function world_rot_bottom(i) = _deg_norm(frame_rot_for_disk(i) + bottom_rot_deg);

// Zone center angle in DISK-LOCAL coordinates
function zone_master_local(i) = _deg_norm(bridge_ang(i) - world_rot_bottom(i));


// Zone center angle for disk i in WORLD coordinates
function box_world_ang(i) = _deg_norm(world_rot_bottom(i) + zone_master_local(i));

// ============================================================
// ROTATIONS (BOTTOM first, TOP second)
// ============================================================
bottom_rot_deg = bottom_disk_step * (360 / bottom_steps_total);

// frame rotation aligns that disk's TARGET big candle label toward +Y (90 deg)
function frame_rot_for_disk(i) =
  let(A = candle_ang(levels[i]))
  (90 - A);

// nails (FIXED polar per disk; no RNG)
// nail_polar[i] = [angle_deg, distance_mm]
// distance is measured from disk center
nail_polar = [
  [220.000000, 11.050000],
  [120.000000, 15.050000],
  [220.000000, 13.050000],
  [50.000000, 14.050000]
];
function nail_ang(i) = nail_polar[i][0];
function nail_dist(i) = nail_polar[i][1];

// top rotation aligns top line (local +Y) to nail direction
function top_rot_for_disk(i) =
  let(fr = frame_rot_for_disk(i))
  _deg_norm(nail_ang(i) - fr - pointer_ang_local_deg);

// TOP ZONES MUST USE BOTTOM AS WORLD REFERENCE:
function zone_local_top(i, j) =
  _deg_norm(bottom_rot_deg + zone_master_local(j) - top_rot_for_disk(i));


//TOP: 13 red, 80 + 97 green, 210 + 227 yellow
//BOT: 340 red, 5 + 15 green, 55 blue, 145 yellow
//rest are random
global_top_marks = [
  // INSIDE (drives the solution totals)
  20,
  305, 315,
  55,
  220, 230,

  // OUTSIDE (decorative / non-counting, but unambiguous)
  171.551,
  272.737,
  140.867,
  99.228,
  341.915,
  259.851
];
global_bottom_marks = [
  // INSIDE (drives the solution totals)
  332,
  12, 20,
  144,

  // OUTSIDE (decorative / non-counting, but unambiguous)
  272.864,
  184.059,
  282.167,
  109.193,
  210.018,
  170.596,
  241.864,
  89.368,
  194.146,
  300.525,
  230.619,
  257.651
];

// ============================================================
// CLOSED SECTOR POLYGON (NO CGAL "not closed")
// ============================================================
function _arc_points(r, a1, a2, steps) =
  [ for (k=[0:steps]) let(t=k/steps, a=a1 + (a2-a1)*t) [r*cos(a), r*sin(a)] ];

module sector2d_poly(r, center_deg, halfspan_deg, steps=32) {
  a1 = center_deg - halfspan_deg;
  a2 = center_deg + halfspan_deg;
  pts = concat([[0,0]], _arc_points(r, a1, a2, steps));
  polygon(points=pts);
}

module sector3d(r, h, center_deg, halfspan_deg, z0=0) {
  translate([0,0,z0])
    linear_extrude(height=h)
      sector2d_poly(r, center_deg, halfspan_deg, steps=40);
}

// Side-wall line marks (raised bars)
module wall_line_marks(r, z0, h, angles_local) {
  z1 = z0 + wall_mark_z_inset;
  hh = max(0.01, h - 2*wall_mark_z_inset);

  for (a = angles_local) {
    rotate([0,0,a])
      translate([r, -wall_mark_w/2, z1])
        color([0,0,0])
          cube([wall_mark_out, wall_mark_w, hh], center=false);
  }
}

// ============================================================
// GEOMETRY
// ============================================================

// ============================================================
// BOARD MARKERS (engraved into wood) — ZONE CONES (as you drew)
// Disk 0/1/2: draw a "cone" that EXTENDS the corresponding zone outward from the hole.
// Disk 3: unchanged (no marker).
//
// IMPORTANT: players see NO printed colors/zones on the disks.
// These cones are the ONLY board cue.
// In CAD we render the engraved area as a darker wood tone (not green).
//
// Also add a tiny universal hint: 3 small "tally bars" inside the cone,
// to suggest "COUNT the wall bars inside this projected slice".
//
// FULL FILE. ASCII ONLY.
// ============================================================

show_board_markers = true;      // engrave cones into the board
marker_depth       = 0.6;       // mm (engrave depth into top surface)

// Cone geometry (project the hidden zone outward)
cone_r_start_clear = 0.6;       // mm: start just OUTSIDE disk outer radius
cone_len = 5;         // mm: how far the cone extends outward
cone_arc_steps     = 28;        // polygon resolution for the sector edges

// Visual-only: fill the engraved area with a darker wood tone in the preview (so it's NOT green)
show_marker_fill_preview = true;
marker_fill_th           = 0.25;      // mm visual "ink" thickness
marker_fill_col          = [0.42,0.28,0.18];

// NEW: "look-through bridge" hint (small sight window)
// Idea: a thin vertical frame whose opening has the SAME width as the zone at the disk radius,
// and the SAME height as the two stacked disks. Players can "peek through" to isolate the zone.
show_zone_bridge   = true;
bridge_th          = 2.0;       // mm thickness (radial)
bridge_frame_w     = 1.2;       // mm frame border (tangential + vertical)
bridge_clear_h     = 0.2;       // mm height clearance above disks
bridge_clear_r     = 0.4;       // mm radial clearance so bridge doesn't touch disk
bridge_r_pos       = 0.55;      // 0..1 position along the cone length (where the bridge sits)
bridge_h_scale     = 1.08;      // height multiplier (5-10% extra over (bottom_h+top_h))

// FIXED bridge polar per disk; no RNG
// bridge_polar[i] = [angle_deg, distance_mm] (from disk center)

// (bridge_polar moved earlier)



// Small gap so the cone doesn't touch the disk edge visually
function _cone_r0() = token_bottom_d/2 + cone_r_start_clear;
function _cone_r1() = _cone_r0() + cone_len;

// 2D ring-sector (center at origin), from radius r0..r1, angle a0..a1 (degrees)
module _ring_sector2d(r0, r1, a0, a1, steps=24) {
  pts_outer = [for (k=[0:steps]) [ r1*cos(a0 + (a1-a0)*k/steps), r1*sin(a0 + (a1-a0)*k/steps) ]];
  pts_inner = [for (k=[steps:-1:0]) [ r0*cos(a0 + (a1-a0)*k/steps), r0*sin(a0 + (a1-a0)*k/steps) ]];
  polygon(concat(pts_outer, pts_inner));
}

// Cone angle for disk i = its OWN zone center in WORLD space
function _zone_center_world(i) = box_world_ang(i);

// 2D marker shape in LOCAL coordinates (origin at hole center)
// Rotate by the zone center, then build a sector from -halfspan..+halfspan.
module _zone_cone_marker2d(i) {
  rotate(_zone_center_world(i))
    _ring_sector2d(_cone_r0(), _cone_r1(), -zone_halfspan_deg, +zone_halfspan_deg, cone_arc_steps);
}

module _cut_zone_cone(i) {
  if (show_board_markers) {
    cx = x_of_i(i);
    cy = hole_y();

    translate([0,0, base_th - marker_depth])
      linear_extrude(height=marker_depth + 0.02)
        translate([cx, cy])
          _zone_cone_marker2d(i);
  }
}

// Visual fill for the engraved area (darker wood tone), so it's NOT green in CAD.
module _fill_zone_cone_preview(i) {
  if (show_board_markers && show_marker_fill_preview) {
    cx = x_of_i(i);
    cy = hole_y();

    color(marker_fill_col)
      translate([0,0, base_th - marker_depth + 0.001])
        linear_extrude(height=marker_fill_th)
          translate([cx, cy])
            _zone_cone_marker2d(i);
  }
}

// --- Bridge geometry ---
// Opening width = zone chord width at disk outer radius.
// Height = bottom + top disk heights (plus a tiny clearance).
function _zone_open_w() =
  let(r = token_bottom_d/2)
  (2 * r * sin(zone_halfspan_deg));

function _bridge_open_h() = (token_bottom_h + token_top_h) * bridge_h_scale;
function _bridge_outer_w() = _zone_open_w() + 2*bridge_frame_w;
function _bridge_outer_h() = _bridge_open_h() + 2*bridge_frame_w;

module _zone_bridge(i) {
  if (show_zone_bridge) {
    cx = x_of_i(i);
    cy = hole_y();

    // Place bridge at the OUTER END of the cone (as requested).
    // IMPORTANT: Our cone marker is centered on local angle 0deg (= +X axis).
    // So after rotate([0,0,ang]), local +X points radially outward along the zone.
    // Move bridge closer to disk: 70% closer than outer cone end
    rpos = bridge_dist(i);
    ang  = bridge_ang(i);

    open_w  = _zone_open_w();
    open_h  = _bridge_open_h();
    outer_w = open_w + 2*bridge_frame_w;        // tangential span (Y)
    outer_h = open_h + bridge_frame_w;          // only top beam, no bottom beam (U-bridge)

    translate([cx, cy, base_th])
      rotate([0,0,ang])
        translate([rpos, 0, 0]) {

          // U-BRIDGE: two posts + top beam. OPEN AT BOTTOM.
          // Frame axes here:
          //   X = radial thickness
          //   Y = tangential width (zone width)
          //   Z = height

          // Left post (at -Y)
          translate([-bridge_th/2, -outer_w/2, 0])
            cube([bridge_th, bridge_frame_w, outer_h], center=false);

          // Right post (at +Y)
          translate([-bridge_th/2, outer_w/2 - bridge_frame_w, 0])
            cube([bridge_th, bridge_frame_w, outer_h], center=false);

          // Top beam (spans full tangential width)
          translate([-bridge_th/2, -outer_w/2, outer_h - bridge_frame_w])
            cube([bridge_th, outer_w, bridge_frame_w], center=false);
        }
  }
}

module board_markers_cut() {
  // as requested: cones only on disks 0/1/2 (disk 3 unchanged)
  _cut_zone_cone(0);
  _cut_zone_cone(1);
  _cut_zone_cone(2);
}

module board_markers_fill_preview() {
  _fill_zone_cone_preview(0);
  _fill_zone_cone_preview(1);
  _fill_zone_cone_preview(2);
}

module board_bridges() {
  // bridges on disks 0..3
  _zone_bridge(0);
  _zone_bridge(1);
  _zone_bridge(2);
  _zone_bridge(3);
}

module board() {

  difference() {
    color([0.62,0.42,0.26])
      translate([-((3*candle_spacing) + 2*base_margin_x)/2, -(base_margin_y_front + front_offset_y), 0])
        cube([(3*candle_spacing) + 2*base_margin_x, base_margin_y_back + base_margin_y_front + front_offset_y, base_th]);
    for (i=[0:3]) {
      translate([x_of_i(i), hole_y(), 0])
        cylinder(d=hole_d + hole_clear, h=hole_depth);
    }

    // engraved markers (disk0=opt1, disk1=opt2, disk2=opt3)
    board_markers_cut();
  }
  // Visual-only: darker fill for engraved cones
  board_markers_fill_preview();

  // Physical hint: small look-through bridges (0/1/2 only)
  color([0.55,0.36,0.22]) board_bridges();
}
module candle_real_with_number(x=0, y=0, d=22, lvl=0, label="0") {
  h = level_to_h(lvl);
  translate([x, y, base_th]) {
    color([0.95,0.92,0.82]) cylinder(d=d, h=h);

    color([0.08,0.05,0.02]) translate([0,0,h]) cylinder(d=d*0.08, h=2.4);
    color([1.0,0.55,0.10])  translate([0,0,h+2.2]) scale([1,1,1.6]) sphere(d=d*0.18);

    if (show_big_numbers) {
      zc = h*0.55;
      rad = d/2 - big_num_side_inset;
      color([0.12,0.10,0.08])
        translate([0, -rad, zc])
          rotate([90,0,0])
            linear_extrude(height=big_num_th)
              text(label, size=big_num_size, font=font_name, halign="center", valign="center");
    }
  }
}

// Small candle icon CENTERED at origin
module annulus_candle_icon_centered(level=0, label="0") {
  body_h = ann_body_h0 + level * ann_body_h_step;

  union() {
    color([1.0, 0.95, 0.65])
      linear_extrude(height=mark_th)
        square([ann_body_w, body_h], center=true);

    if (show_annulus_numbers) {
      color([0.15, 0.12, 0.08])
        translate([0, 0, mark_th])
          linear_extrude(height=ann_num_th)
            text(label, size=ann_num_size, font=font_name, halign="center", valign="center");
    }

    top_y = body_h/2 + ann_flame_y_gap;

    color([1.0, 0.55, 0.10])
      linear_extrude(height=mark_th)
        translate([0, top_y]) {
          union() {
            translate([0, ann_flame_h*0.35]) circle(r=ann_flame_w*0.48, $fn=24);
            polygon(points=[
              [0, ann_flame_h*1.15],
              [-ann_flame_w*0.45, 0],
              [ ann_flame_w*0.45, 0]
            ]);
          }
        }
  }
}

module annulus_candles_on_ring() {
  bottom_r = token_bottom_d/2;
  top_r    = token_top_d/2;    
  ring_mid_r = ((bottom_r + top_r)/2)-((ann_flame_h + ann_flame_y_gap)/2);
  // Auto scale so icons stay fully within the annulus band:
  // - never cross outer edge (bottom_r)
  // - never intrude into inner edge (top_r / top disk)
  max_lvl = 3;
  body_h_max = ann_body_h0 + max_lvl * ann_body_h_step;
    
    ring_bot_r = top_r + (ann_body_h0/2);

  // Icon extents along +Y (flame side) and -Y (body bottom) in its LOCAL frame
  y_in  = body_h_max/2;  // inward extent (toward center)  : bottom of body
  y_out = body_h_max/2 + ann_flame_y_gap + ann_flame_h*1.15; // outward extent : flame tip

  s_in  = (ring_mid_r - (top_r + annulus_margin)) / y_in;
  s_out = ((bottom_r - annulus_margin) - ring_mid_r) / y_out;

  ann_s_auto = _clamp(min([s_in, s_out, 1.0]), 0.01, 1.0);
  ann_s = annulus_auto_scale ? ann_s_auto : annulus_scale_manual;

  for (p=[0:3]) {
    rotate([0,0,candle_ang(p)]) {
      translate([(ring_bot_r + 0.1 + (ann_body_h_step*p)/2), 0, token_bottom_h + mark_z_eps]) {
        scale([ann_s, ann_s, ann_s])
          rotate([0,0,-90])
          annulus_candle_icon_centered(p, str(p));
      }
    }
  }
}

// Disk: base gray + colored sectors (full height => walls colored too)
// SHOW FILTER: only show zone == show_only_idx if show_only_idx >= 0
module disk_with_zones_bottom(r, h, z0=0, show_only_idx=-1) {
  color(gray_disk) translate([0,0,z0]) cylinder(r=r, h=h);

  for (j=[0:3]) if (show_only_idx < 0 || j == show_only_idx) {
    local_center = zone_master_local(j);
    color(zone_colors[j])
      sector3d(r, h + 0.001, local_center, zone_halfspan_deg, z0);
  }
}

// Top disk zones use bottom as world reference => use zone_local_top(i,j)
// SHOW FILTER: only show zone == show_only_idx if show_only_idx >= 0
module disk_with_zones_top(r, h, z0=0, disk_index=0, show_only_idx=-1) {
  color(gray_disk) translate([0,0,z0]) cylinder(r=r, h=h);

  for (j=[0:3]) if (show_only_idx < 0 || j == show_only_idx) {
    local_center = zone_local_top(disk_index, j);
    color(zone_colors[j])
      sector3d(r, h + 0.001, local_center, zone_halfspan_deg, z0);
  }
}

// black line on top disk (local +Y)
// Top-disk pointer (FIXED polar; no RNG)
function pointer_r(top_r) = top_r; // could be shorter if desired

module top_disk_pointer(top_r) {
  line_w = 0.7;
  line_l = pointer_r(top_r);
  line_h = 0.35;

  color([0,0,0])
    translate([0, 0, token_bottom_h + token_top_h - line_h + 0.01])
      rotate([0,0,pointer_ang_local_deg - 90]) // make 0deg point to +X, so rotate into +Y-based strip
        linear_extrude(height=line_h)
          translate([-line_w/2, 0])
            square([line_w, line_l], center=false);
}

module disk_assembly(i) {
  bottom_r = token_bottom_d/2;
  top_r    = token_top_d/2;

  union() {
    // bottom (apply bottom rotation ONLY)
    rotate([0,0,bottom_rot_deg]) {
      // show only owned zone i (red/green/blue/yellow per disk index)
      disk_with_zones_bottom(bottom_r, token_bottom_h, 0, i);
      annulus_candles_on_ring();
      wall_line_marks(bottom_r, 0, token_bottom_h, global_bottom_marks);
    }

    // top (independent rotation)
    rotate([0,0,top_rot_for_disk(i)]) {
      // show only owned zone i (but still computed the same)
      disk_with_zones_top(top_r, token_top_h, token_bottom_h, i, i);
      top_disk_pointer(top_r);
      wall_line_marks(top_r, token_bottom_h, token_top_h, global_top_marks);
    }
  }
}

module nail_at_polar(ang_deg, rr) {
color([0.15,0.15,0.15]) {
    rotate([0,0,ang_deg])
      translate([rr, 0, base_th])
        cylinder(r=nail_r, h=nail_h);

    rotate([0,0,ang_deg])
      translate([rr, 0, base_th + nail_h])
        cylinder(r=nail_head_r, h=nail_head_h);
  }
}

module nails_one_per_hole() {
  for (i=[0:3]) {
    translate([x_of_i(i), hole_y(), 0])
      nail_at_polar(nail_ang(i), nail_dist(i));
  }
}

module all_disks_in_holes() {
  for (i=[0:3]) {
    cx = x_of_i(i);
    cy = hole_y();
    fr = frame_rot_for_disk(i);

    translate([cx, cy, base_th]) {
      rotate([0,0,fr]) disk_assembly(i);
    }
  }
}

// ============================================================
// SCENE
// ============================================================
module scene() {
  if (show_board) board();

  for (i=[0:3]) {
    lvl = levels[i];
    candle_real_with_number(x_of_i(i), candle_y(), candle_d, lvl, str(lvl));
  }

  if (show_disks) all_disks_in_holes();
  if (show_nails) nails_one_per_hole();
}

// ==================== BAKE OUTPUT ====================
echo("=== BAKE constellation_id = 16 ===");

echo("candle_angles =", candle_angles);
echo("box_local_angles =", box_local_angles);

echo("nail_ang =", [
  nail_ang(0),
  nail_ang(1),
  nail_ang(2),
  nail_ang(3)
]);

echo("top_rot_for_disk =", [
  top_rot_for_disk(0),
  top_rot_for_disk(1),
  top_rot_for_disk(2),
  top_rot_for_disk(3)
]);

echo("global_bottom_marks =", global_bottom_marks);
echo("global_top_marks =", global_top_marks);

echo("zone_halfspan_deg =", zone_halfspan_deg);
echo("levels =", levels);


scene();