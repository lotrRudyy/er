// Combined starfield + selectable zodiac constellation (no-overlap)
// Small stars come from your extracted purple-star map (2mm holes)
// Zodiac constellation holes are larger (hole_d * zodiac_mult) and will REMOVE any small star that would overlap.
//
// Select constellation by index 0..6:
// 0=Leo, 1=Pisces, 2=Libra, 3=Aquarius, 4=Sagittarius, 5=Scorpio, 6=Gemini
//
// Print disk: 200mm outer, 170mm inner reference circle (only outline)
//

$fn = 128;

// ---------------- USER SETTINGS ----------------
constellation_idx = 6;     // 0..6
mode = "3D";               // "2D" for SVG/DXF export, "3D" for clean preview

disk_d = 200;              // outer disk diameter (mm)
map_d  = 170;              // inner reference circle diameter (mm) (all stars stay within this)
disk_t = 1.2;              // only used in 3D preview

hole_d = 2.0;              // small star hole diameter (mm)
zodiac_mult = 2.5;         // zodiac holes are hole_d * zodiac_mult
clearance = 0.2;           // extra safety gap between holes (mm)

show_outer_disk  = true;   // show filled brown disk (preview)
show_inner_ring  = true;   // show 170mm outline (black)
show_center_cross = true;

// Zodiac placement (ONLY rigid transform: uniform scale + rotation + translation)
zodiac_rot_deg = 0;
zodiac_offset_xy = [0, 0];     // mm
fit_margin = 6;                // mm margin to keep zodiac inside map circle

// ---------------- DATA ----------------
// small star field (mm, centered at origin)
star_xy = [
  [-27.744, 68.246],
  [56.916, 60.136],
  [-60.857, 57.901],
  [-42.013, 28.437],
  [54.238, 26.798],
  [-67.640, 25.542],
  [7.216, 22.436],
  [-9.154, 20.919],
  [34.729, 11.284],
  [-64.364, -3.308],
  [43.938, -13.229],
  [59.843, -18.015],
  [-12.413, -18.632],
  [24.350, -27.166],
  [-49.229, -34.457],
  [48.440, -40.378],
  [-2.194, -49.547],
  [-19.550, -56.218],
  [25.488, -60.749]
];

// zodiac constellation points (normalized-ish) from your reference images
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
  [-0.265730, 0.935982],
  [-0.910998, 0.412412],
  [0.447751, 0.412070],
  [-0.228618, 0.379872],
  [-0.533603, -0.178623],
  [0.594546, -0.298915],
  [0.571898, -0.482816],
  [0.736040, -0.522184],
  [-0.411287, -0.657797]
] :
    i==3 ? [
  [0.134438, 0.354616],
  [-0.444567, -0.086040],
  [0.060856, -0.237296],
  [0.358331, 0.204015],
  [-0.231237, 0.204713],
  [-0.647851, -0.208661],
  [0.084861, 0.029295],
  [-0.310564, -0.002495],
  [0.537010, -0.117032],
  [-0.396486, 0.377165],
  [0.855211, -0.518281]
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
  [0.349408, 0.245638],
  [-0.657482, -0.100573],
  [-0.457085, -0.685758],
  [0.798321, 0.602232],
  [-0.867315, -0.482710],
  [-0.328561, -0.138758],
  [-0.098498, -0.410898],
  [0.727870, 0.262947],
  [0.058649, 0.112954],
  [0.474693, 0.594926]
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


// ---------------- MATH HELPERS ----------------
function vlen2(v) = v[0]*v[0] + v[1]*v[1];
function rot2(p, a) = let(c=cos(a), s=sin(a)) [c*p[0]-s*p[1], s*p[0]+c*p[1]];
function max_radius(pts) = max([for(p=pts) sqrt(vlen2(p))]);

function xform_pts(pts, scale, rot_deg, off) =
    [ for(p=pts) let(q=rot2([p[0]*scale, p[1]*scale], rot_deg)) [q[0]+off[0], q[1]+off[1]] ];

function zodiac_pts_mm() =
    let(pts = pts_for(constellation_idx))
    let(rmax = max_radius(pts))
    let(r_target = (map_d/2 - fit_margin))
    let(scale = (rmax > 0 ? (r_target / rmax) : 1))
    xform_pts(pts, scale, zodiac_rot_deg, zodiac_offset_xy);

// minimum squared distance from p to a list of points
function min_dist2(p, pts, i=0, best=1e18) =
    (i >= len(pts)) ? best :
    let(dx = p[0]-pts[i][0], dy = p[1]-pts[i][1], d2 = dx*dx + dy*dy)
    min_dist2(p, pts, i+1, (d2 < best ? d2 : best));

// does small star overlap any zodiac star?
function keep_small_star(p, zpts) =
    let(r_small = hole_d/2)
    let(r_big   = (hole_d*zodiac_mult)/2)
    let(th = r_small + r_big + clearance)
    (min_dist2(p, zpts) >= th*th);

// ---------------- DRAW ----------------
module inner_ring_2d() {
    // black outline ring (area ring, not a line)
    ring_w = 0.6; // mm
    difference() {
        circle(d=map_d + ring_w);
        circle(d=map_d - ring_w);
    }
}

module center_cross_2d() {
    union() {
        square([0.25, map_d*0.08], center=true);
        square([map_d*0.08, 0.25], center=true);
    }
}

module holes_2d() {
    zpts = zodiac_pts_mm();

    // small stars (filtered so they NEVER overlap zodiac holes)
    for (p = star_xy)
        if (keep_small_star(p, zpts))
            translate(p) circle(d=hole_d);

    // zodiac stars (bigger)
    for (p = zpts)
        translate(p) circle(d=hole_d*zodiac_mult);
}

module scene_2d() {
    if (show_outer_disk) {
        color([0.55,0.35,0.18]) circle(d=disk_d);
    }
    // cut holes out of disk
    if (show_outer_disk) {
        difference() {
            circle(d=disk_d);
            holes_2d();
        }
    } else {
        // just show holes without disk
        holes_2d();
    }

    if (show_inner_ring)
        color([0,0,0]) inner_ring_2d();

    if (show_center_cross)
        color([0,0,0]) center_cross_2d();
}

if (mode == "2D") {
    scene_2d();
} else {
    // 3D preview (avoids OpenCSG zebra artifacts)
    difference() {
        color([0.55,0.35,0.18]) linear_extrude(height=disk_t) circle(d=disk_d);
        translate([0,0,-0.1]) linear_extrude(height=disk_t+0.2) holes_2d();
    }
    if (show_inner_ring)
        color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=0.2) inner_ring_2d();
    if (show_center_cross)
        color([0,0,0]) translate([0,0,disk_t+0.01]) linear_extrude(height=0.2) center_cross_2d();
}
