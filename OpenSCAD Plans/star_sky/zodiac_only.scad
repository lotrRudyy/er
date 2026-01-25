// Auto-generated from your constellation reference images
// Select constellation by index 0..6 (no existing starfield included)
// 0=Leo, 1=Pisces, 2=Libra, 3=Aquarius, 4=Sagittarius, 5=Scorpio, 6=Gemini

$fn = 96;

constellation_idx = 3;     // 0..6
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
scale_extra = 1.0;         // 1.0 = fill available map circle

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

    // scale so max radius becomes (map_d/2 - fit_margin)
    r_target = (map_d/2 - fit_margin) * scale_extra;

    translate(offset_xy)
    rotate(rot_deg)
    for(p = pts) {
        translate([p[0]*r_target, p[1]*r_target])
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
