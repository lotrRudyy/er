// OpenSCAD 2021.01
// Pin5 Mount – PRINT (standalone) | OPTION 1: SNAP-IN ROD (slot + blind holes)
// AXES (local):
// X = Tiefe (hooks an ±X Rand)
// Y = Eisenstab-Richtung (zwischen Türmen)
// Z = Höhe

$fn = 128;
eps = 0.02;

// -------------------------
// TOWERS  (YOUR UPDATED PARAMS)
// -------------------------
tower_w   = 8.5;   // Y thickness
tower_H   = 32;    // Z height
tower_D   = 22;    // X depth
inner_gap = 18;    // distance between INNER faces in Y

L_total = 2*tower_w + inner_gap;

// -------------------------
// BASE
// -------------------------
base_H = 8;
base_x_overhang = 32;

base_Y = L_total;
base_X = tower_D + 2*base_x_overhang;

// -------------------------
// ROD SEATS (BLIND, 3.5mm)
// -------------------------
rod_hole_d = 3.2;
rod_axis_z = 22;

rod_len = 25; // informational only

rod_embed_left  = 3.5;
rod_embed_right = 3.5;

rod_x = 0;

// -------------------------
// SNAP SLOT (OPTION 1) – reaches hole boundary
// -------------------------
slot_enable       = true;
slot_clear_w      = 3.40;  // inner channel width (loose)
slot_entry_w      = 2.80;  // mouth width (tight)
slot_entry_depth  = 1.20;  // mouth depth from outer +X face

// -------------------------
// MOUNT HOLES
// -------------------------
mount_enable = true;
mount_hole_d = 3.6;
mount_x_edge_inset = 20;
mount_y_edge_inset = 7;

// -------------------------
// HOOKS
// -------------------------
hook_y_span = 15;
hook_depthX = 24 * (2/3);
pocket_Z    = 3;
pocket_X    = 16 * (2/3);
hook_H      = pocket_Z + 4;

// ============================================================
// CUTS
// ============================================================

// 2 BLIND holes (each from INNER face of its tower) along Y
module rod_holes_cut_blind() {
    zC = base_H + rod_axis_z;

    // Inner faces:
    yInnerL = -inner_gap/2;
    yInnerR = +inner_gap/2;

    // LEFT tower material is at y < yInnerL, so drill toward -Y.
    // rotate([90,0,0]) makes cylinder axis point -Y (because +Z becomes -Y).
    translate([rod_x, yInnerL + eps, zC])
        rotate([90,0,0])
            cylinder(d=rod_hole_d, h=rod_embed_left + 2*eps, center=false);

    // RIGHT tower material is at y > yInnerR, so drill toward +Y.
    // rotate([-90,0,0]) makes cylinder axis point +Y (because +Z becomes +Y).
    translate([rod_x, yInnerR - eps, zC])
        rotate([-90,0,0])
            cylinder(d=rod_hole_d, h=rod_embed_right + 2*eps, center=false);
}

// Right tower: cut channel from +X outer face to the rod hole boundary (intersects for sure)
module snap_slot_cut_right_tower() {
    if (slot_enable) {
        yR = +(inner_gap/2 + tower_w/2);
        zC = base_H + rod_axis_z;

        xOuter = +tower_D/2;

        // reach rod hole boundary at x = +rod_hole_d/2
        x_hit = (rod_hole_d/2) - 0.10;

        dx = xOuter - x_hit;
        if (dx > 0) {
            xMid = (x_hit + xOuter)/2;
            translate([xMid, yR, zC])
                cube([dx + 2*eps, tower_w + 2*eps, slot_clear_w + 2*eps], center=true);
        }

        translate([xOuter - slot_entry_depth/2, yR, zC])
            cube([slot_entry_depth + 2*eps, tower_w + 2*eps, slot_entry_w + 2*eps], center=true);
    }
}

module mount_holes_cut() {
    if (mount_enable)
        for (sx=[-1,1], sy=[-1,1])
            translate([
                sx*(base_X/2 - mount_x_edge_inset),
                sy*(base_Y/2 - mount_y_edge_inset),
                base_H/2
            ])
                cylinder(d=mount_hole_d, h=base_H + 2*eps, center=true);
}

// ============================================================
// SOLIDS
// ============================================================

module base_plate() {
    translate([0,0,base_H/2])
        cube([base_X, base_Y, base_H], center=true);
}

module towers() {
    zc = base_H + tower_H/2;

    translate([0, -(inner_gap/2 + tower_w/2), zc])
        cube([tower_D, tower_w, tower_H], center=true);

    translate([0, +(inner_gap/2 + tower_w/2), zc])
        cube([tower_D, tower_w, tower_H], center=true);
}

module hook_block(is_pos_x=true) {
    dir = is_pos_x ? +1 : -1;
    x_edge = dir*(base_X/2);

    x0 = x_edge - dir*hook_depthX;
    x1 = x_edge;

    z0 = base_H;

    difference() {
        translate([(x0+x1)/2, 0, z0 + hook_H/2])
            cube([abs(x1-x0), hook_y_span, hook_H], center=true);

        translate([x0 + dir*(pocket_X/2), 0, z0 + pocket_Z/2])
            cube([pocket_X+2*eps, hook_y_span+2*eps, pocket_Z+2*eps], center=true);
    }
}

module hooks() {
    hook_block(true);
    hook_block(false);
}

module printed_part() {
    difference() {
        union() {
            base_plate();
            towers();
            hooks();
        }
        mount_holes_cut();
        rod_holes_cut_blind();
        snap_slot_cut_right_tower();
    }
}

// ============================================================
// RENDER
// ============================================================

color([0.62,0.50,0.34]) printed_part();
