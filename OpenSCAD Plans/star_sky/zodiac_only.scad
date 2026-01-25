// Auto-generated from your constellation reference images
// Select constellation by index 0..6 (no existing starfield included)
// 0=Leo, 1=Pisces, 2=Libra, 3=Aquarius, 4=Sagittarius, 5=Scorpio, 6=Gemini

$fn = 96;

constellation_idx = 0;     // 0..6
mode = "3D";               // "2D" for SVG/DXF export, "3D" for clean preview
disk_d = 200;              // mm (outer disk)
map_d  = 170;              // mm (inner reference circle diameter)
disk_t = 1.0;              // mm (only used in 3D)
hole_d = 2.0;              // mm base
zodiac_mul = 2.5;
zodiac_d = hole_d * zodiac_mul;

ring_w = 0.6;              // mm (inner ring line thickness)
show_outer_disk = true;
show_inner_ring = true;
show_center_cross = true;

// Placement controls (do NOT change constellation geometry; only rigid transform + uniform scale)
fit_margin = 2.0;          // mm safety margin to stay inside map_d
rot_deg = 0;               // rotate constellation
offset_xy = [0,0];         // mm offset inside disk (x,y)
scale_extra = 0.9;         // 1.0 = fill available map circle


// -------------------- helpers (OpenSCAD has no built-in sum/reduce) --------------------
function fsum(v, i=0, acc=0) = (i >= len(v)) ? acc : fsum(v, i+1, acc + v[i]);
function vlen2(v) = sqrt(v[0]*v[0] + v[1]*v[1]);
// ---------- Ritter smallest-enclosing-circle style center (good auto-fit)
// returns [cx, cy, r] where r is max distance to center after expansion
function dist2(a,b) = vlen2([a[0]-b[0], a[1]-b[1]]);

function farthest_from(p, pts, i=0, best_i=0, best_d=-1) =
    (i >= len(pts)) ? best_i :
    let(d = dist2(p, pts[i]))
    farthest_from(p, pts, i+1, (d > best_d) ? i : best_i, (d > best_d) ? d : best_d);

function ritter_seed(pts) =
    let(i1 = farthest_from(pts[0], pts),
        i2 = farthest_from(pts[i1], pts),
        p1 = pts[i1],
        p2 = pts[i2],
        c  = [(p1[0]+p2[0])/2, (p1[1]+p2[1])/2],
        r  = dist2(c, p1))
    [c[0], c[1], r];

function ritter_step(pts, i, cx, cy, r) =
    (i >= len(pts)) ? [cx, cy, r] :
    let(p = pts[i],
        d = dist2([cx,cy], p))
    (d <= r) ?
        ritter_step(pts, i+1, cx, cy, r) :
        let(new_r = (r + d)/2,
            // move center toward p
            k = (d - r) / (2*d),
            new_cx = cx + (p[0]-cx)*k,
            new_cy = cy + (p[1]-cy)*k)
        ritter_step(pts, i+1, new_cx, new_cy, new_r);

function ritter_circle(pts) =
    let(seed = ritter_seed(pts),
        res  = ritter_step(pts, 0, seed[0], seed[1], seed[2]))
    res;

// -------------------- Constellation point sets (normalized) --------------------
function pts_for(i) =
    i==0 ? [
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
  [-0.547937,  0.090230],
  [-0.348317,  0.073318],
  [-0.506610, -0.175535],
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

// -------------------- Helpers --------------------
module outer_disk_2d() {
    color([0.55,0.35,0.18]) circle(d=disk_d);
}

module inner_ring_2d() {
    // black ring (area ring, not a line)
    color([0,0,0])
    difference() {
        circle(d=map_d + ring_w);
        circle(d=map_d - ring_w);
    }
}

module center_cross_2d() {
    color([0,0,0])
    union() {
        square([0.25, map_d*0.08], center=true);
        square([map_d*0.08, 0.25], center=true);
    }
}

module zodiac_holes_2d() {
    pts = pts_for(constellation_idx);

    // ---- Auto-fit (NO distortion): recentre -> uniform-scale to fill map circle ----
    // This fixes the "unused space" problem: we do NOT assume pts are already centred.
    n = len(pts);

    // better auto-center: Ritter enclosing circle center (fills the 170mm ring much better than centroid)
    rc = ritter_circle(pts);
    cx = rc[0];
    cy = rc[1];
    maxr = rc[2];  // already the max distance from (cx,cy)

    // desired radius inside the 170mm reference circle
    r_target = (map_d/2 - fit_margin) * scale_extra;

    // uniform scale factor
    s = (maxr > 0) ? (r_target / maxr) : 1;

    translate(offset_xy)
    rotate(rot_deg)
    for(p = pts) {
        translate([(p[0]-cx)*s, (p[1]-cy)*s])
            circle(d=zodiac_d);
    }
}

module scene_2d() {
    if (show_outer_disk) outer_disk_2d();
    if (show_inner_ring) inner_ring_2d();
    if (show_center_cross) center_cross_2d();

    // Cut the zodiac holes out of the disk (or just show them if outer disk off)
    if (show_outer_disk)
        difference() {
            // disk fill again to ensure holes cut cleanly
            outer_disk_2d();
            zodiac_holes_2d();
        }
    else
        zodiac_holes_2d();
}

if (mode == "2D") {
    scene_2d();
} else {
    // 3D: extrude for clean preview (avoids OpenCSG stripe artifacts)
    difference() {
        color([0.55,0.35,0.18]) linear_extrude(height=disk_t) circle(d=disk_d);
        translate([0,0,-0.1]) linear_extrude(height=disk_t+0.2) zodiac_holes_2d();
    }
    if (show_inner_ring)
        color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=0.2) inner_ring_2d();
    if (show_center_cross)
        color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=0.2) center_cross_2d();
}