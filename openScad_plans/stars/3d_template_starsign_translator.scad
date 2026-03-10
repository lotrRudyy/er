// ============================================================
// all_stars_sky_merged_better_template.scad
//
// Based on: all_stars_sky_merged_better.scad
// Units: mm, origin at disk center (0,0)
// Disk reference: 200mm diameter, inner mapping circle: 170mm diameter
//
// WHAT'S NEW
// - template_only: when true, generates a thin print template (not a chunky disk)
// - template_fast: when true, prints ONLY:
//     * a thin outer reference ring, plus
//     * small "pads" around each hole location
//   This is MUCH faster than printing a full solid disk.
// - Keeps ALL coordinate data (purple + all zodiac constellations)
// ============================================================

$fn = 128;

// ===================== USER SETTINGS =====================
constellation_idx = 2;     // 0=Leo,1=Pisces,2=Libra,3=Aquarius,4=Sagittarius,5=Scorpio,6=Gemini,7=Only Purple
mode = "3D";               // "2D" for SVG/DXF export, "3D" for preview/printing

// Reference geometry
disk_d = 180;              // mm outer reference diameter
map_d  = 170;              // mm inner reference circle diameter

// Hole diameters
purple_hole_d = 3.0;       // mm
zodiac_hole_d = 7.5;       // mm

// Visual helpers
show_map_ring  = true;     // show inner reference ring (map_d)
show_center_x  = true;     // show center crosshair

// Ring/cross styling (also used in template)
ring_w  = 0.6;             // mm ring outline width (radial)
cross_L = 14;              // mm
cross_w = 0.35;            // mm

// ============ TEMPLATE MODES ============
// If you are 3D-printing a template to mark star positions, set template_only=true.
// template_fast=true makes it dramatically faster.

template_only = true;
template_fast = true;

// Template thickness (3D print)
// 0.6–1.0mm is usually perfect for "marking jig" templates.
template_t = 0.8;

// Fast template geometry
fast_outer_ring_w = 6.0;   // mm radial width of the outer reference ring
fast_pad_d        = 10.0;  // mm diameter of pads around each hole


// Fast template connectors (to keep everything one single piece)
add_spokes      = true;  // if true: connect each pad to the outer ring with a thin spoke
spoke_w         = 1.0;   // mm spoke thickness (0.8–1.2 works well)

// Extra spoke reinforcement (tangential left/right)
add_side_spokes = true;  // adds 2 additional spokes per pad (left & right) for a stronger connection
side_spoke_deg  = 7;     // degrees offset along the ring for the side spokes (4–10 is typical)
// If you want the template to be *even faster*, increase layer height in your slicer.

// ===================== FIXED COORDINATES =====================
purple_fixed = [
  [-27.744, 62.246],
  [56.916, 54.136],
  [-60.857, 51.901],
  [31, 36],
  [-42.013, 22.437],
  [54.238, 20.798],
  [-67.640, 19.542],
  [7.216, 16.436],
  [-9.154, 14.919],
  [34.729, 5.284],
  [-64.364, -9.308],
  [43.938, -19.229],
  [59.843, -24.015],
  [-12.413, -24.632],
  [24.350, -33.166],
  [-49.229, -40.457],
  [48.440, -46.378],
  [-2.194, -55.547],
  [-19.550, -62.218],
  [25.488, -66.749]
]
;

zodiac_fixed = [
  // 0 — Leo
  [
    [13.055, 22.962],
    [-38.608, -27.258],
    [-74.287, -34.720],
    [13.069, 5.107],
    [40, 46.767],
    [-38.529, -0.446],
    [26.701, -8.100],
    [33, -31],
    [55, 31.582],
    [-61, 6]
  ],

  // 1 — Pisces
  [
    [-28.802, 50.366],
    [-26.325, 70.883],
    [-60.469, -4.275],
    [67.782, -33.562],
    [-57.654, -18.356],
    [53.767, -18.476],
    [65.595, -14.714],
    [-46.613, 16.984],
    [-77.828, -25.823],
    [75.411, -25.220],
    [37.148, -14.966],
    [-21.553, -11.180],
    [53.401, -32.356]
  ],

  // 2 — Libra
  [
    [57, 42],
    [-20, 32],
    [-30, 0],
    [-52, 6],
    [51, -7.691],
    [11, -27],
    [16, -58]
  ],

  // 3 — Aquarius
  [
    [-70, 35],
    [-60, 7],
    [-12, 24],
    [-40, -2],
    [-51, -21],
    [-75, -31],
    [-30, -55],
    [-15, -58],
    [-17, -40],
    [5, -43],
    [-17, -15],
    [33, -12],
    [68, 38] // de led isch kaputt glab i
  ],

  // 4 — Sagittarius
  [
    [19.739, -38.007],
    [18.154, 1.675],
    [-32.482, 16.582],
    [-1.740, 37.343],
    [-57.597, -15.760],
    [20.771, 79.326],
    [-71.290, 0.389],
    [-52.752, 17.685],
    [47.204, 1.361],
    [34.845, -56.271]
  ],

  // 5 — Scorpio
  [
    [-25, -47],
    [-53, -60],
    [-62, -41],
    [-54, -21],
    [-47, 2],
    [-6, -9.5],
    [67, 40],
    [41, 50],
    [20, 48],
    [37, 19]
  ],

  // 6 — Gemini
  [
    [-55.679, 59.192],
    [12.404, 23.570],
    [-80.654, 14.798],
    [66.675, 8.243],
    [45.290, 3.918],
    [-38.106, -24.917],
    [50.927, -56.387]
  ],

  // 7 — Only Purple
  [
  ]
]
;

// ===================== 2D HELPERS =====================
module disk2d(d) { circle(d=d); }

module ring2d(d_outer, d_inner) {
  difference() {
    circle(d=d_outer);
    circle(d=d_inner);
  }
}

module map_ring2d(d, w) {
  difference() {
    circle(d=d + w*2);
    circle(d=d - w*2);
  }
}

module holes2d(pts, d) {
  for(p=pts) translate(p) circle(d=d);
}

module pads2d(pts, d_pad) {
  for(p=pts) translate(p) circle(d=d_pad);
}


// Spokes: connect pads to the outer ring so the fast template prints as ONE piece (no loose islands)
module spoke2d(p, r_target, w=spoke_w, a_off=0) {
  x = p[0]; y = p[1];
  a = atan2(y, x) + a_off; // degrees
  // capsule via hull of two small circles
  hull() {
    translate([x,y]) circle(d=w);
    translate([cos(a)*r_target, sin(a)*r_target]) circle(d=w);
  }
}

module spokes2d(pts, r_target, w=spoke_w) {
  for(p=pts) {
    // center spoke
    spoke2d(p, r_target, w, 0);
    // side spokes (left/right along ring)
    if(add_side_spokes) {
      spoke2d(p, r_target, w,  side_spoke_deg);
      spoke2d(p, r_target, w, -side_spoke_deg);
    }
  }
}


module crosshair2d(L=cross_L, w=cross_w) {
  translate([-L/2,0]) square([L, w], center=true);
  translate([0,-L/2]) square([w, L], center=true);
}

// ===================== TEMPLATE (2D) =====================
module template_body_2d(Z) {
  if(template_fast) {
    // Outer reference ring + small pads around each hole location
    union() {
      ring2d(disk_d, disk_d - 2*fast_outer_ring_w);
      pads2d(purple_fixed, fast_pad_d);
      pads2d(Z,           fast_pad_d);

      // Keep everything connected as ONE printable piece:
      // spokes go from each pad to the midline of the outer ring.
      if(add_spokes) {
        r_mid = disk_d/2 - fast_outer_ring_w/2;
        spokes2d(purple_fixed, r_mid, spoke_w);
        spokes2d(Z,           r_mid, spoke_w);
      }
    }
  } else {
    // Full disk (still thin in 3D if template_only=true)
    disk2d(disk_d);
  }
}

module template_scene_2d() {
  Z = zodiac_fixed[constellation_idx];

  difference() {
    template_body_2d(Z);
    // punch holes
    holes2d(purple_fixed, purple_hole_d);
    holes2d(Z,           zodiac_hole_d);

    // optional: make the center crosshair a cutout so it’s easy to align
    if(show_center_x) crosshair2d();
  }

  // optional: add the inner reference ring as a visible 2D outline
  if(show_map_ring)
    map_ring2d(map_d, ring_w);
}

// ===================== TEMPLATE (3D) =====================
module template_scene_3d() {
  Z = zodiac_fixed[constellation_idx];

  // Main thin body with cut holes
  difference() {
    linear_extrude(height=template_t) template_body_2d(Z);

    // holes go fully through
    translate([0,0,-0.2])
      linear_extrude(height=template_t + 0.4) {
        holes2d(purple_fixed, purple_hole_d);
        holes2d(Z,           zodiac_hole_d);

        if(show_center_x) crosshair2d();
      }
  }

  // Optional: inner reference ring as a *raised* ridge (tiny height, still quick)
  if(show_map_ring)
    translate([0,0,template_t])
      linear_extrude(height=0.25) map_ring2d(map_d, ring_w);
}

// ===================== ORIGINAL-LIKE MODES =====================
// If you ever want the old behavior (solid thick disk), set template_only=false.

module legacy_scene_2d() {
  Z = zodiac_fixed[constellation_idx];

  difference() {
    disk2d(disk_d);
    holes2d(purple_fixed, purple_hole_d);
    holes2d(Z,           zodiac_hole_d);
  }

  if(show_map_ring) map_ring2d(map_d, ring_w);
  if(show_center_x) crosshair2d();
}

module legacy_scene_3d() {
  // Kept for compatibility; similar to your original file but uses template_t as thickness
  Z = zodiac_fixed[constellation_idx];
  disk_t = 2.5;

  difference() {
    linear_extrude(height=disk_t) circle(d=disk_d);
    translate([0,0,-0.2])
      linear_extrude(height=disk_t + 0.4) {
        holes2d(purple_fixed, purple_hole_d);
        holes2d(Z,           zodiac_hole_d);
      }
  }

  if(show_map_ring)
    translate([0,0,disk_t])
      linear_extrude(height=0.4) map_ring2d(map_d, ring_w);

  if(show_center_x)
    translate([0,0,disk_t + 0.4 + 0.01])
      linear_extrude(height=0.2) crosshair2d();
}

// ===================== RUN =====================
if(template_only) {
  if(mode == "2D") template_scene_2d(); else template_scene_3d();
} else {
  if(mode == "2D") legacy_scene_2d(); else legacy_scene_3d();
}

