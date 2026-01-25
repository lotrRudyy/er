// Starfield (purple) + Zodiac constellation (select idx 0..6)
// - Outer disk: 200mm diameter
// - Inner mapping circle: 170mm diameter (black outline)
// - Purple stars: 2.0mm holes (from your extracted starfield)
// - Zodiac stars: larger holes (2.5x diameter by default)
// - IMPORTANT: No overlaps: zodiac is auto-placed (rotate + uniform scale + if needed small offset) to avoid purple holes.
// OpenSCAD 2021.01 compatible (no sum(), no norm()).

$fn = 128;

// ===================== USER SETTINGS =====================
constellation_idx = 0;   // 0=Leo,1=Pisces,2=Libra,3=Aquarius,4=Sagittarius,5=Scorpio,6=Gemini
mode = "2D";             // "2D" for SVG/DXF export, "3D" for clean preview

disk_d = 200;            // mm outer disk diameter
map_d  = 170;            // mm inner reference circle diameter
disk_t = 2.0;            // mm thickness for 3D preview

purple_hole_d = 2.0;                 // mm
zodiac_hole_d = purple_hole_d * 2.5; // mm (bigger than purple)

show_disk_fill   = true;
show_map_ring    = true;
show_center_x    = true;

ring_w = 0.6;            // mm ring outline width (radial)
t_ring = 0.4;            // mm ring thickness in 3D
cross_L = 14;            // mm
cross_w = 0.35;          // mm

// Purple starfield pre-fit controls (same as your purple file)
y_nudge_mm = -6;         // move ALL purple stars down a bit (negative = down)
safety_mm  = 1.0;        // keep at least this much inside the 170mm circle

// Zodiac auto-placement controls
clearance_mm = 0.2;      // extra clearance between holes
fit_margin_mm = 0.5;     // keep zodiac this far inside the 170mm circle (in addition to hole radius)
rot_step_deg = 15;       // rotation search step
off_step_mm  = 6;        // offset grid step (mm)
off_steps    = 2;        // number of steps each direction (2 => (-12, -6, 0, 6, 12))
prefer_center = true;    // if true: minimize offset distance (most central)

// ===================== PURPLE STARFIELD (mm, from your extracted photo) =====================
purple_xy_raw = [

[
    -27.744,
    68.246
  ],
  [
    56.916,
    60.136
  ],
  [
    -60.857,
    57.901
  ],
  [
    -42.013,
    28.437
  ],
  [
    54.238,
    26.798
  ],
  [
    -67.64,
    25.542
  ],
  [
    7.216,
    22.436
  ],
  [
    -9.154,
    20.919
  ],
  [
    34.729,
    11.284
  ],
  [
    -64.364,
    -3.308
  ],
  [
    43.938,
    -13.229
  ],
  [
    59.843,
    -18.015
  ],
  [
    -12.413,
    -18.632
  ],
  [
    24.35,
    -27.166
  ],
  [
    -49.229,
    -34.457
  ],
  [
    48.44,
    -40.378
  ],
  [
    -2.194,
    -49.547
  ],
  [
    -19.55,
    -56.218
  ],
  [
    25.488,
    -60.749
  ]

];

// Helpers for purple fit
function vlen2(v) = v[0]*v[0] + v[1]*v[1];
function vlen(v) = sqrt(vlen2(v));
function max_r(pts) = max([for(p=pts) vlen(p)]);

function apply_nudge_and_fit(pts) =
    let(
        pts2 = [for(p=pts) [p[0], p[1] + y_nudge_mm]],
        rmax = max_r(pts2),
        rlim = (map_d/2) - safety_mm,
        s    = (rmax > 0) ? min(1, rlim / rmax) : 1
    )
    [for(p=pts2) [p[0]*s, p[1]*s]];

purple_xy = apply_nudge_and_fit(purple_xy_raw);

// ===================== ZODIAC POINT SETS (normalized) =====================
function pts_for(i) = i==0 ? [
  [0.159204, 0.280024],
  [-0.470832, -0.332411],
  [-0.905933, -0.423420],
  [0.159377, 0.062284],
  [0.384366, 0.509348],
  [-0.469868, -0.005438],
  [0.325618, -0.098778],
  [0.340847, -0.376759],
  [0.477220, 0.385149]
] :
    i==1 ? [
  [-0.351247, 0.614223],
  [-0.321037, 0.864424],
  [-0.737433, -0.052136],
  [0.826613, -0.409298],
  [-0.703101, -0.223856],
  [0.655700, -0.225313],
  [0.799937, -0.179441],
  [-0.568454, 0.207128],
  [-0.949119, -0.314918],
  [0.919643, -0.307567],
  [-0.412916, 0.740204],
  [0.453020, -0.182517],
  [-0.262841, -0.136341],
  [0.651237, -0.394591]
] :
    i==2 ? [
  [0.788211, 0.615405],
  [-0.030439, 0.565854],
  [-0.925424, 0.200731],
  [-0.404700, 0.150556],
  [0.498672, -0.230722],
  [0.041565, -0.450217],
  [0.032115, -0.851607]
] :
    i==3 ? [
  [ 0.532463,  0.846453],
  [ 0.103697,  0.590352],
  [-0.353483,  0.336667],
  [ 0.429146,  0.182040],
  [ 0.022247,  0.104726],
  [-0.647937,  0.090230],
  [-0.348317,  0.073318],
  [-0.606610, -0.175535],
  [ 0.196682, -0.180367],
  [-0.070329, -0.310834],
  [ 0.279336, -0.412308],
  [ 0.625449, -0.541566],
  [-0.162346, -0.603176]
] :
    i==4 ? [
  [0.105784, -0.479338],
  [0.205953, -0.035308],
  [-0.310430, 0.279943],
  [0.091516, 0.418596],
  [-0.684427, -0.003616],
  [0.465285, 0.816620],
  [-0.788118, 0.215790],
  [-0.531576, 0.352285],
  [0.526644, -0.124967],
  [0.218853, -0.726367]   // removed non-star bottom-right point

] :
    i==5 ? [
  [ 0.415757,  0.909476],
  [ 0.548812,  0.685938],
  [ 0.183061,  0.522443],
  [ 0.603793,  0.403053],
  [-0.049366,  0.078819],
  [-0.539698, -0.225253],
  [ 0.106515, -0.459940],
  [-0.666177, -0.592623],
  [-0.178672, -0.605792],
  [-0.424026, -0.716122]
] :
           [
  [-0.679016,  0.721854],
  [ 0.151268,  0.287445],
  [-0.983582,  0.180462],
  [ 0.813107,  0.100525],
  [ 0.552315,  0.047778],
  [-0.464707, -0.303866],
  [-0.010446, -0.346546],
  [ 0.621060, -0.687652]
]
;

// ===================== GEOMETRY HELPERS =====================
function rot2(p, a) = [
    p[0]*cos(a) - p[1]*sin(a),
    p[0]*sin(a) + p[1]*cos(a)
];

function add2(a,b) = [a[0]+b[0], a[1]+b[1]];

// Compute max radius of normalized points
function max_r_norm(pts) = max([for(p=pts) sqrt(p[0]*p[0] + p[1]*p[1])]);

// Transform zodiac normalized points into mm with rotation + uniform scale + offset
function zodiac_xform_pts(i, a, off) =
    let(
        pts = pts_for(i),
        rmax = max_r_norm(pts),
        // available radius inside map circle after accounting for zodiac hole radius + margin
        r_avail = (map_d/2) - (zodiac_hole_d/2) - fit_margin_mm,
        s0 = (rmax > 0) ? (r_avail / rmax) : 1
    )
    [for(p=pts) add2( rot2([p[0]*s0, p[1]*s0], a), off )];

// distance^2 between two points
function dist2(a,b) = (a[0]-b[0])*(a[0]-b[0]) + (a[1]-b[1])*(a[1]-b[1]);

// Minimum distance^2 between any point in A and any point in B
function min_dist2_AB(A,B) =
    min([for(a=A, b=B) dist2(a,b)]);

// Max radius^2 of a set (distance from origin)
function max_r2(A) = max([for(a=A) vlen2(a)]);

// Candidate validity: no overlap with purple + stays within map circle
function cand_ok(i, a, off) =
    let(
        Z = zodiac_xform_pts(i, a, off),
        // overlap threshold
        dmin = (purple_hole_d/2) + (zodiac_hole_d/2) + clearance_mm,
        ok_overlap = (min_dist2_AB(Z, purple_xy) >= dmin*dmin),
        // boundary: all zodiac centers must be within map_d/2 - zodiac_radius - margin
        r_lim = (map_d/2) - (zodiac_hole_d/2) - fit_margin_mm,
        ok_bound = (max_r2(Z) <= r_lim*r_lim + 1e-6)
    )
    (ok_overlap && ok_bound);

// Build candidate list [score,a,ox,oy]
function cand_list(i) =
    [for(a=[0:rot_step_deg:359],
         ix=[-off_steps:off_steps],
         iy=[-off_steps:off_steps])
        let(ox = ix*off_step_mm, oy = iy*off_step_mm, off=[ox,oy])
        if (cand_ok(i,a,off))
            [ (prefer_center ? (ox*ox + oy*oy) : 0) , a, ox, oy ]
    ];

// Recursive best-index by smallest score
function best_idx(L, idx=0, best=0) =
    (len(L)==0) ? 0 :
    (idx >= len(L)) ? best :
    best_idx(L, idx+1, (L[idx][0] < L[best][0]) ? idx : best);

// Get chosen transform (fallback to 0,0 if no valid candidate)
function best_pose(i) =
    let(L = cand_list(i))
    (len(L)==0) ? [0,0,0] : let(b = L[best_idx(L)]) [b[1], b[2], b[3]];

// ===================== DRAWING MODULES =====================
module disk2d(d) { circle(d=d); }

module map_ring2d(d, w) { difference() { circle(d=d+w*2); circle(d=d-w*2); } }

module holes2d(pts, d) { for(p=pts) translate(p) circle(d=d); }

module crosshair2d(L=cross_L, w=cross_w) {
  translate([-L/2,0]) square([L, w], center=true);
  translate([0,-L/2]) square([w, L], center=true);
}

module scene2d() {
  // base disk with holes cut
  if(show_disk_fill)
    color([0.55,0.35,0.18])
      difference() {
        disk2d(disk_d);
        holes2d(purple_xy, purple_hole_d);
        // zodiac
        pose = best_pose(constellation_idx);
        Z = zodiac_xform_pts(constellation_idx, pose[0], [pose[1], pose[2]]);
        holes2d(Z, zodiac_hole_d);
      }
  else {
    holes2d(purple_xy, purple_hole_d);
    pose = best_pose(constellation_idx);
    Z = zodiac_xform_pts(constellation_idx, pose[0], [pose[1], pose[2]]);
    holes2d(Z, zodiac_hole_d);
  }

  if(show_map_ring)
    color([0,0,0]) map_ring2d(map_d, ring_w);
  if(show_center_x)
    color([0,0,0]) crosshair2d();
}

module scene3d() {
  pose = best_pose(constellation_idx);
  Z = zodiac_xform_pts(constellation_idx, pose[0], [pose[1], pose[2]]);

  difference() {
    color([0.55,0.35,0.18]) linear_extrude(height=disk_t) circle(d=disk_d);
    translate([0,0,-1]) linear_extrude(height=disk_t+2) {
      holes2d(purple_xy, purple_hole_d);
      holes2d(Z, zodiac_hole_d);
    }
  }

  if(show_map_ring)
    color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=t_ring) map_ring2d(map_d, ring_w);
  if(show_center_x)
    color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=t_ring) crosshair2d();
}

if(mode=="2D") scene2d(); else scene3d();
