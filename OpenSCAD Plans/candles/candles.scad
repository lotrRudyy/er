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

seed_candles = 12345;
seed_boxes   = 54321;
seed_nails   = 777;

seed_lines   = 9999;   // deterministic wall mark RNG

// wall "line marks" on disk side walls (top + bottom).
// IMPORTANT: there is ONE physical token disk -> marks are IDENTICAL for all 4 rendered placements.
wall_marks_per_wall = 20;      // per wall count (bottom wall has this many, top wall has this many)
wall_mark_min_sep_deg = 6;     // separation between marks on same wall
wall_mark_min_sep_deg_inside  = 2.6;   // MIN sep for COUNTING marks inside zones (allows 4 marks)
wall_mark_min_sep_deg_outside = wall_mark_min_sep_deg; // sep for non-counting/outside marks
wall_mark_w   = 0.425;         // tangential width (HALVED)
wall_mark_out = 0.55;          // radial protrusion outward from cylinder wall
wall_mark_z_inset = 0.12;      // keep marks away from wall edges

// Clear counting rules:
line_in_edge_clear_deg     = 6.0;   // buffer (deg) beyond half thickness for marks that COUNT inside a zone
line_outside_clear_deg     = 12.0;  // buffer beyond half thickness for marks that must be OUTSIDE a zone

// Required per hole i (L->R): inside its OWN color zone (red/green/blue/yellow),
// the TOTAL number of marks on BOTH walls (bottom+top walls) inside that zone equals:
zone_solution_counts = [2,4,1,3];

candle_min_sep_deg = 58;

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

token_bottom_d   = 18.1;
token_bottom_h   = 2.3;

token_top_d      = 14.2;
token_top_h      = 2.3;

// annulus candles (2D icons)
mark_th        = 0.25;
mark_z_eps     = 0.06;

ann_body_w       = 1.6;
ann_body_h0      = 2.0;
ann_body_h_step  = 1.6;

ann_flame_w    = 1.3;
ann_flame_h    = 1.7;
ann_flame_y_gap = 0.25;

font_name = "Liberation Sans:style=Bold";
show_annulus_numbers = true;
ann_num_size  = 1.6;
ann_num_th    = 0.22;

show_big_numbers = true;
big_num_size = 8.5;
big_num_th   = 0.9;
big_num_side_inset = 0.8;

// big candle height levels (L->R)
levels = [0, 2, 3, 1];

// nails
nail_r         = 1.3;
nail_h         = 7.0;
nail_head_r    = 2.3;
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
candle_angles = gen4_spaced_angles_seq(seed_candles, constellation_id, candle_min_sep_deg);
function candle_ang(label) = candle_angles[label];

// Zones (master local angles, non-touching)
box_local_angles = gen4_spaced_angles_seq(seed_boxes, constellation_id, zone_min_sep_deg);
function zone_master_local(j) = box_local_angles[j];

// ============================================================
// ROTATIONS (BOTTOM first, TOP second)
// ============================================================
bottom_rot_deg = bottom_disk_step * (360 / bottom_steps_total);

// frame rotation aligns that disk's TARGET big candle label toward +Y (90 deg)
function frame_rot_for_disk(i) =
  let(A = candle_ang(levels[i]))
  (90 - A);

// nails (raw)
function _nail_ang_raw(i) = _rand_range(0, 360, seed_nails, 20000 + constellation_id*10 + i);

function world_rot_bottom(i) = _deg_norm(frame_rot_for_disk(i) + bottom_rot_deg);

// nail angle: avoid being too close to that disk's OWN zone center in world
function box_world_ang(i) = _deg_norm(world_rot_bottom(i) + zone_master_local(i));

function nail_ang(i) =
  let(
    boxa = box_world_ang(i),
    a0 = _nail_ang_raw(i),
    a1 = (_ang_diff(a0, boxa) < 28) ? _deg_norm(a0 + 55) : a0,
    a2 = (_ang_diff(a1, boxa) < 28) ? _deg_norm(a1 + 55) : a1
  ) a2;

// top rotation aligns top line (local +Y) to nail direction
function top_rot_for_disk(i) =
  let(fr = frame_rot_for_disk(i))
  _deg_norm(nail_ang(i) - fr - 90);

// TOP ZONES MUST USE BOTTOM AS WORLD REFERENCE:
function zone_local_top(i, j) =
  _deg_norm(bottom_rot_deg + zone_master_local(j) - top_rot_for_disk(i));

// ============================================================
// WALL MARKS - ONE PHYSICAL DISK (GLOBAL PATTERN) WITH CLEAR EDGE RULES
// ============================================================
function _in_wedge(a, center, halfspan) = (_ang_diff(a, center) <= halfspan);
function _in_any_wedge(a, centers, halfspan) =
  _all_true([for (c=centers) (!_in_wedge(a, c, halfspan))]) ? false : true;

// Angle (deg) corresponding to half the line's tangential width at radius r
function _half_line_deg(r) = (wall_mark_w/2) / r * 180 / PI;

// Conservative angular half-width (top radius is smaller -> larger degrees)
_bottom_r0 = token_bottom_d/2;
_top_r0    = token_top_d/2;
_half_line_deg_max = max(_half_line_deg(_bottom_r0), _half_line_deg(_top_r0));

// Effective margins so at least half thickness is clearly in/out
inside_margin_deg  = _half_line_deg_max + line_in_edge_clear_deg;
outside_margin_deg = _half_line_deg_max + line_outside_clear_deg;

// pick a random angle within [center-halfspan+margin, center+halfspan-margin]
function _pick_in_wedge(seed, base_idx, center, halfspan, margin, attempt) =
  let(
    t = _prand01(seed, base_idx + attempt),
    a = center + (t*2 - 1) * (halfspan - margin)
  ) _deg_norm(a);

// pick any
function _pick_any(seed, base_idx, attempt) = _pick_angle(seed, base_idx, attempt);

// Bounded attempt picker: returns angle or undef if not found
function _try_pick_angle(seed, base_idx, attempt, attempt_max,
                         want_inside, center, halfspan, inset_margin,
                         avoid_centers, avoid_halfspan,
                         existing, minsep) =
  (attempt > attempt_max) ? undef :
  let(
    a0 = want_inside ? _pick_in_wedge(seed, base_idx, center, halfspan, inset_margin, attempt)
                     : _pick_any(seed, base_idx, attempt),
    ok_inside = want_inside ? _in_wedge(a0, center, halfspan - inset_margin) : true,
    ok_avoid  = (len(avoid_centers) == 0) ? true : (!_in_any_wedge(a0, avoid_centers, avoid_halfspan)),
    ok_sep    = _all_true([for (e=existing) _ang_diff(a0, e) >= minsep])
  )
  ((ok_inside && ok_avoid && ok_sep) ? a0
                                    : _try_pick_angle(seed, base_idx, attempt+1, attempt_max,
                                                      want_inside, center, halfspan, inset_margin,
                                                      avoid_centers, avoid_halfspan,
                                                      existing, minsep));

// Generate up to n angles; may return fewer if bounded attempts fail
function _gen_angles_bounded(seed, base_idx, n, attempt_max,
                            want_inside, center, halfspan, inset_margin,
                            avoid_centers, avoid_halfspan,
                            minsep, existing=[], k=0) =
  (k >= n) ? [] :
  let(
    a = _try_pick_angle(seed, base_idx + k*137, 0, attempt_max,
                        want_inside, center, halfspan, inset_margin,
                        avoid_centers, avoid_halfspan,
                        existing, minsep)
  )
  (a == undef) ? [] :
  let(
    rest = _gen_angles_bounded(seed, base_idx, n, attempt_max,
                               want_inside, center, halfspan, inset_margin,
                               avoid_centers, avoid_halfspan,
                               minsep, concat(existing,[a]), k+1)
  )
  concat([a], rest);

// Deterministic desired split (deficits moved to BOTTOM)
function split_bottom_for_hole(i) =
  let(t = zone_solution_counts[i])
  _clamp(floor(_prand01(seed_lines, 70000 + constellation_id*31 + i) * (t+1)), 0, t);

function split_top_for_hole(i) = zone_solution_counts[i] - split_bottom_for_hole(i);

top_need_desired = [split_top_for_hole(0), split_top_for_hole(1), split_top_for_hole(2), split_top_for_hole(3)];

// TOP wedge centers in TOP-local space for each hole i (counting ONLY its owned color i)
function top_center(i) = zone_local_top(i, i);
top_centers = [top_center(0), top_center(1), top_center(2), top_center(3)];

// Strict avoid span: must be outside other holes' wedges plus a strong buffer
avoid_span_strict = zone_halfspan_deg + outside_margin_deg;

// Build TOP marks per hole i with strict avoidance of the other 3 windows
top_marks_0 = _gen_angles_bounded(seed_lines, 73000 + constellation_id*1000 + 0*211, top_need_desired[0], 3000,
                                 true,  top_centers[0], zone_halfspan_deg, inside_margin_deg,
                                 [top_centers[1],top_centers[2],top_centers[3]], avoid_span_strict,
                                 wall_mark_min_sep_deg_inside, []);

top_marks_1 = _gen_angles_bounded(seed_lines, 73100 + constellation_id*1000 + 1*211, top_need_desired[1], 3000,
                                 true,  top_centers[1], zone_halfspan_deg, inside_margin_deg,
                                 [top_centers[0],top_centers[2],top_centers[3]], avoid_span_strict,
                                 wall_mark_min_sep_deg_inside, top_marks_0);

top_marks_2 = _gen_angles_bounded(seed_lines, 73200 + constellation_id*1000 + 2*211, top_need_desired[2], 3000,
                                 true,  top_centers[2], zone_halfspan_deg, inside_margin_deg,
                                 [top_centers[0],top_centers[1],top_centers[3]], avoid_span_strict,
                                 wall_mark_min_sep_deg_inside, concat(top_marks_0,top_marks_1));

top_marks_3 = _gen_angles_bounded(seed_lines, 73300 + constellation_id*1000 + 3*211, top_need_desired[3], 3000,
                                 true,  top_centers[3], zone_halfspan_deg, inside_margin_deg,
                                 [top_centers[0],top_centers[1],top_centers[2]], avoid_span_strict,
                                 wall_mark_min_sep_deg_inside, concat(top_marks_0,top_marks_1,top_marks_2));

top_required = concat(top_marks_0, top_marks_1, top_marks_2, top_marks_3);

// Actual placed top counts:
top_have = [len(top_marks_0), len(top_marks_1), len(top_marks_2), len(top_marks_3)];

// BOTTOM needs are the remaining required marks per hole
bottom_need = [
  zone_solution_counts[0] - top_have[0],
  zone_solution_counts[1] - top_have[1],
  zone_solution_counts[2] - top_have[2],
  zone_solution_counts[3] - top_have[3]
];

// extras counts
bottom_req_total = bottom_need[0]+bottom_need[1]+bottom_need[2]+bottom_need[3];
top_req_total    = top_have[0]+top_have[1]+top_have[2]+top_have[3];

bottom_extra = (wall_marks_per_wall > bottom_req_total) ? (wall_marks_per_wall - bottom_req_total) : 0;
top_extra    = (wall_marks_per_wall > top_req_total)    ? (wall_marks_per_wall - top_req_total)    : 0;

// BOTTOM wedge centers are fixed and non-touching
bottom_centers = [zone_master_local(0), zone_master_local(1), zone_master_local(2), zone_master_local(3)];

// Build BOTTOM required marks per hole (inside its own wedge with inset)
bottom_marks_0 = _gen_angles_bounded(seed_lines, 71000 + constellation_id*1000 + 0*211, bottom_need[0], 2000,
                                    true,  bottom_centers[0], zone_halfspan_deg, inside_margin_deg,
                                    [], 0,
                                    wall_mark_min_sep_deg_inside, []);

bottom_marks_1 = _gen_angles_bounded(seed_lines, 71100 + constellation_id*1000 + 1*211, bottom_need[1], 2000,
                                    true,  bottom_centers[1], zone_halfspan_deg, inside_margin_deg,
                                    [], 0,
                                    wall_mark_min_sep_deg_inside, bottom_marks_0);

bottom_marks_2 = _gen_angles_bounded(seed_lines, 71200 + constellation_id*1000 + 2*211, bottom_need[2], 2000,
                                    true,  bottom_centers[2], zone_halfspan_deg, inside_margin_deg,
                                    [], 0,
                                    wall_mark_min_sep_deg_inside, concat(bottom_marks_0,bottom_marks_1));

bottom_marks_3 = _gen_angles_bounded(seed_lines, 71300 + constellation_id*1000 + 3*211, bottom_need[3], 2000,
                                    true,  bottom_centers[3], zone_halfspan_deg, inside_margin_deg,
                                    [], 0,
                                    wall_mark_min_sep_deg_inside, concat(bottom_marks_0,bottom_marks_1,bottom_marks_2));

bottom_required = concat(bottom_marks_0,bottom_marks_1,bottom_marks_2,bottom_marks_3);

// bottom extras: outside all bottom wedges with strong buffer (use outside sep)
bottom_extras = _gen_angles_bounded(seed_lines, 72000 + constellation_id*1000, bottom_extra, 4000,
                                   false, 0, 0, 0,
                                   bottom_centers, zone_halfspan_deg + outside_margin_deg,
                                   wall_mark_min_sep_deg_outside, bottom_required);

global_bottom_marks = concat(bottom_required, bottom_extras);

// top extras: outside all top windows with strong buffer (use outside sep)
top_extras = _gen_angles_bounded(seed_lines, 74000 + constellation_id*1000, top_extra, 4000,
                                false, 0, 0, 0,
                                top_centers, zone_halfspan_deg + outside_margin_deg,
                                wall_mark_min_sep_deg_outside, top_required);

global_top_marks = concat(top_required, top_extras);

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
module board() {
  difference() {
    color([0.62,0.42,0.26])
      translate([-((3*candle_spacing) + 2*base_margin_x)/2, -(base_margin_y_front + front_offset_y), 0])
        cube([(3*candle_spacing) + 2*base_margin_x, base_margin_y_back + base_margin_y_front + front_offset_y, base_th]);

    for (i=[0:3]) {
      translate([x_of_i(i), hole_y(), 0])
        cylinder(d=hole_d + hole_clear, h=hole_depth);
    }
  }
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
  ring_mid_r = (bottom_r + top_r)/2;

  for (p=[0:3]) {
    rotate([0,0,candle_ang(p)]) {
      translate([ring_mid_r, 0, token_bottom_h + mark_z_eps]) {
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
module top_disk_line(top_r) {
  line_w = 0.7;
  line_l = top_r;
  line_h = 0.35;

  color([0,0,0])
    translate([0, 0, token_bottom_h + token_top_h - line_h + 0.01])
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
      top_disk_line(top_r);
      wall_line_marks(top_r, token_bottom_h, token_top_h, global_top_marks);
    }
  }
}

module nail_at_angle(ang_deg) {
  bottom_r = token_bottom_d/2;
  rr = bottom_r + nail_offset_from_disk;

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
      nail_at_angle(nail_ang(i));
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
