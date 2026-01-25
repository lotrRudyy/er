// OpenSCAD 2021.01
// Pin5 Mount – LIBRARY (Option A)

$fn = 96;
eps = 0.02;

// -------------------------
// UPDATED CORE PARAMS
// -------------------------
pin5_inner_gap = 18;

pin5_tower_w = 8.5;
pin5_tower_H = 32;
pin5_tower_D = 22;

pin5_L_total = 2*pin5_tower_w + pin5_inner_gap;

// Rod
pin5_rod_d = 3.0;
pin5_rod_len = 25;
pin5_rod_hole_d = 3.2;
pin5_rod_axis_z = 22;

pin5_rod_embed_left  = 3.5;
pin5_rod_embed_right = pin5_rod_len - pin5_inner_gap - pin5_rod_embed_left;

pin5_rod_x = 0;

// Base
pin5_base_H = 8;
pin5_base_x_overhang = 32;
pin5_base_Y = pin5_L_total;
pin5_base_X = pin5_tower_D + 2*pin5_base_x_overhang;

// Hooks
pin5_hook_y_span = 15;
pin5_hook_depthX = 24*(2/3);
pin5_pocket_Z = 3;
pin5_pocket_X = 16*(2/3);
pin5_hook_H = pin5_pocket_Z + 4;

// Mount holes
pin5_mount_hole_d = 3.6;
pin5_mount_x_edge_inset = 20;
pin5_mount_y_edge_inset = 7;

// ============================================================
// PUBLIC HELPERS
// ============================================================
function pin5_mount_base_X() = pin5_base_X;
function pin5_mount_base_Y() = pin5_base_Y;
function pin5_mount_rod_axis_Z() = pin5_base_H + pin5_rod_axis_z;

function pin5_mount_anchor_right() =
    [ +pin5_base_X/2 - pin5_hook_depthX, 0, pin5_base_H + pin5_pocket_Z/2 ];

function pin5_mount_anchor_left() =
    [ -pin5_base_X/2 + pin5_hook_depthX, 0, pin5_base_H + pin5_pocket_Z/2 ];

// ============================================================
// INTERNAL GEOMETRY
// ============================================================

module _rod_holes() {
    translate([pin5_rod_x, -pin5_inner_gap/2 - eps, pin5_base_H + pin5_rod_axis_z])
        rotate([90,0,0])
            cylinder(d=pin5_rod_hole_d, h=pin5_rod_embed_left + 2*eps);

    translate([pin5_rod_x, +pin5_inner_gap/2 + eps, pin5_base_H + pin5_rod_axis_z])
        rotate([-90,0,0])
            cylinder(d=pin5_rod_hole_d, h=pin5_rod_embed_right + 2*eps);
}

module _base() {
    translate([0,0,pin5_base_H/2])
        cube([pin5_base_X, pin5_base_Y, pin5_base_H], center=true);
}

module _towers() {
    zc = pin5_base_H + pin5_tower_H/2;

    translate([0, -(pin5_inner_gap/2 + pin5_tower_w/2), zc])
        cube([pin5_tower_D, pin5_tower_w, pin5_tower_H], center=true);

    translate([0, +(pin5_inner_gap/2 + pin5_tower_w/2), zc])
        cube([pin5_tower_D, pin5_tower_w, pin5_tower_H], center=true);
}

module _hook(is_pos_x=true) {
    dir = is_pos_x ? +1 : -1;
    x_edge = dir*(pin5_base_X/2);
    x0 = x_edge - dir*pin5_hook_depthX;
    x1 = x_edge;

    difference() {
        translate([(x0+x1)/2, 0, pin5_base_H + pin5_hook_H/2])
            cube([abs(x1-x0), pin5_hook_y_span, pin5_hook_H], center=true);

        translate([(x0 + dir*pin5_pocket_X/2), 0, pin5_base_H + pin5_pocket_Z/2])
            cube([pin5_pocket_X+2*eps, pin5_hook_y_span+2*eps, pin5_pocket_Z+2*eps], center=true);
    }
}

module _printed() {
    difference() {
        union() {
            _base();
            _towers();
            _hook(true);
            _hook(false);
        }
        _rod_holes();
    }
}

// ============================================================
// PUBLIC MODULE
// ============================================================

module pin5_mount_structure(show_part=true, show_rod=false,
                            part_col=[0.62,0.50,0.34],
                            rod_col=[0.55,0.55,0.55]) {
    if (show_part) color(part_col) _printed();
}
