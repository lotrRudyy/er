// OpenSCAD 2021.01
// Pin5 Mount – Hooks at ±X base edge + clean pocket (no step)
// Current version: C opens to center ✅
//
// AXES:
// X = Tiefe (Hooks an ±X Rand der Base)
// Y = Eisenstab-Richtung (zwischen Türmen)
// Z = Höhe
//
// FULL runnable file.

$fn = 96;
eps = 0.02;

// ============================================================
// BASELINE GEOMETRY
// ============================================================

// Towers
tower_w = 11;     // Y thickness
tower_H = 35;     // Z height
tower_D = 22;     // X depth
inner_gap = 14;   // between inner faces in Y
L_total = 2*tower_w + inner_gap;

// Rod
rod_d = 3.0;
rod_len = 22.0;
rod_hole_d = 3.2;
rod_axis_z = 25.0;
rod_embed_left  = 4.0;
rod_embed_right = rod_len - inner_gap - rod_embed_left;
rod_x = 0;        // centered in X

// Base
base_H = 8;                 // Z thickness
base_y_margin = 0;          // base ends at towers in Y
base_x_overhang = 32;       // extra base in ±X

base_Y = L_total + 2*base_y_margin;
base_X = tower_D + 2*base_x_overhang;

// ============================================================
// MOUNT HOLES
// ============================================================

mount_enable = true;
mount_hole_d = 3.6;

mount_x_edge_inset = 20;
mount_y_edge_inset = 7;

// ============================================================
// HOOKS (Einhänghaken)
// ============================================================

// Length in X shorter by 1/3:
hook_depthX = 24 * (2/3);   // was 24 -> now 16
hook_y_span = 15;           // width in Y

// Mouth height in Z = 3mm:
pocket_Z = 3;               // was 8 -> now 3

// Pocket depth in X also shorter by 1/3:
pocket_X = 16 * (2/3);      // was 16 -> now 10.666...

// Top thickness ("Cs oben") reduced by 15%:
// Previously top thickness was 8mm (16-8). 15% less => 6.8mm.
// Now with pocket_Z=3 -> hook_H = 3 + 6.8 = 9.8
hook_H = pocket_Z + 6.8;    // total Z height of hook above base

// Debug
show_part = true;
show_rod  = true;

// ============================================================
// CUTS
// ============================================================

module rod_holes_cut() {
    translate([rod_x, -inner_gap/2 - eps, base_H + rod_axis_z])
        rotate([90,0,0])
            cylinder(d=rod_hole_d, h=rod_embed_left + 2*eps, center=false);

    translate([rod_x, +inner_gap/2 + eps, base_H + rod_axis_z])
        rotate([-90,0,0])
            cylinder(d=rod_hole_d, h=rod_embed_right + 2*eps, center=false);
}

module mount_holes_cut() {
    if (mount_enable) {
        xF = +(base_X/2 - mount_x_edge_inset);
        xB = -(base_X/2 - mount_x_edge_inset);

        yF = +(base_Y/2 - mount_y_edge_inset);
        yB = -(base_Y/2 - mount_y_edge_inset);

        for (x = [xB, xF])
        for (y = [yB, yF]) {
            translate([x, y, base_H/2])
                cylinder(d=mount_hole_d, h=base_H + 2*eps, center=true);
        }
    }
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

// Hook block placed ON the base edge at ±X.
// Pocket opens to the CENTER (inner face of the hook block).
module hook_block_at_edge(is_pos_x=true) {
    dir = is_pos_x ? +1 : -1;

    // outer base edge
    x_edge  = dir * (base_X/2);

    // hook block spans inward from the edge
    x0 = x_edge - dir*hook_depthX;  // inner end of hook block
    x1 = x_edge;                   // outer end of hook block

    y0 = -hook_y_span/2;
    y1 = +hook_y_span/2;

    z0 = base_H;
    z1 = base_H + hook_H;

    difference() {
        // solid block
        translate([(x0+x1)/2, 0, (z0+z1)/2])
            cube([abs(x1-x0), hook_y_span, (z1-z0)], center=true);

        // POCKET CUT: opening at inner face x0 (towards center)
        x_inner = x0;

        px0 = x_inner;
        px1 = x_inner + dir*pocket_X;

        pz0 = z0;
        pz1 = z0 + pocket_Z;

        translate([(px0+px1)/2, 0, (pz0+pz1)/2])
            cube([abs(px1-px0) + 2*eps,
                  hook_y_span + 2*eps,
                  (pz1-pz0) + 2*eps], center=true);
    }
}

module hooks() {
    hook_block_at_edge(true);   // +X edge
    hook_block_at_edge(false);  // -X edge
}

module printed_part() {
    difference() {
        union() {
            base_plate();
            towers();
            hooks();
        }
        mount_holes_cut();
        rod_holes_cut();
    }
}

module rod_solid() {
    z = base_H + rod_axis_z;
    y0 = inner_gap/2 + rod_embed_left;

    translate([rod_x, y0, z])
        rotate([90,0,0])
            cylinder(d=rod_d, h=rod_len, center=false);
}

// ============================================================
// RENDER
// ============================================================

if (show_part) color([0.62,0.50,0.34]) printed_part();
if (show_rod)  color([0.55,0.55,0.55]) rod_solid();
