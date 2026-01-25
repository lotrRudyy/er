// Auto-generated from your marked photo (purple rings)
// Print template: outer disk 200mm, stars constrained to inner 170mm mapping circle
// Holes: 2.0mm
$fn = 128;

// ===================== USER SETTINGS =====================
disk_d = 200;          // full disk diameter (mm)
map_d  = 170;          // stars must lie within this diameter (mm)
hole_d = 2.0;          // hole diameter (mm)

y_nudge_mm = -6;       // move ALL stars down a bit (negative = down). Set 0 to disable.
safety_mm  = 1.0;      // keep at least this much inside the 170mm circle

show_disk_fill = true; // brown filled disk
show_map_ring  = true; // black outline ring for the 170mm circle
show_center_x  = true; // small crosshair

// View/export modes
mode = "2D";           // "3D" (recommended preview) or "2D" (flat export)
t_disk = 1.0;          // thickness for 3D preview/printing (mm)
t_ring = 0.4;          // ring thickness (mm)
ring_w = 0.6;          // ring radial width (mm) - visible outline thickness

// ===================== STAR COORDS (mm, centered) =====================
// These are the extracted star positions in mm space (already roughly centered).
star_xy = [
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


// ===================== HELPERS =====================
function vlen(v) = sqrt(v[0]*v[0] + v[1]*v[1]);
function max_r(pts) = max([for(p=pts) vlen(p)]);

// Apply y-nudge, then rescale so EVERYTHING stays within map_d/2 - safety_mm
function apply_nudge_and_fit(pts) =
    let(
        pts2 = [for(p=pts) [p[0], p[1] + y_nudge_mm]],
        rmax = max_r(pts2),
        rlim = (map_d/2) - safety_mm,
        s    = (rmax > 0) ? min(1, rlim / rmax) : 1
    )
    [for(p=pts2) [p[0]*s, p[1]*s]];

// Final star positions used for holes
star_fit = apply_nudge_and_fit(star_xy);

// ===================== GEOMETRY =====================
module disk2d(d) { circle(d=d); }

module map_ring2d(d, w) { difference() { circle(d=d+w*2); circle(d=d-w*2); } }

module holes2d(pts, d) { for(p=pts) translate(p) circle(d=d); }

module crosshair2d(r=6, w=0.35) {
  translate([-r,0]) square([2*r, w], center=true);
  translate([0,-r]) square([w, 2*r], center=true);
}

// 2D drawing (for SVG/DXF)
module template2d() {
  // Brown filled disk with holes cut
  if(show_disk_fill)
    color([0.55,0.35,0.18]) difference() { disk2d(disk_d); holes2d(star_fit, hole_d); }

  // Inner mapping circle outline (black), drawn ON TOP
  if(show_map_ring)
    color([0,0,0]) map_ring2d(map_d, ring_w);

  // Optional center mark (thin black)
  if(show_center_x)
    color([0,0,0]) crosshair2d();
}

// 3D version (prevents OpenCSG striping artifacts)
module template3d() {
  // Base disk (brown), holes through
  if(show_disk_fill)
    color([0.55,0.35,0.18])
      difference() {
        linear_extrude(height=t_disk) disk2d(disk_d);
        translate([0,0,-1]) linear_extrude(height=t_disk+2) holes2d(star_fit, hole_d);
      }

  // Inner ring as a thin raised annulus so it's clearly visible
  if(show_map_ring)
    color([0,0,0])
      translate([0,0,t_disk+0.02])
        linear_extrude(height=t_ring) map_ring2d(map_d, ring_w);

  if(show_center_x)
    color([0,0,0])
      translate([0,0,t_disk+0.02])
        linear_extrude(height=t_ring) crosshair2d();
}

if(mode=="2D") template2d(); else template3d();
