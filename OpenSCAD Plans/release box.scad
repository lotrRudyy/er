// box_only.scad  (OpenSCAD 2021.01)
// Release/Reset Box – standalone visualizer (no main boards)
//
// Features included (based on what we discussed):
// - Closed box shell (toggle to open by removing the front face)
// - 4-point brown hang points (top corners)
// - Two top inlets centered, side-by-side (red + yellow)
// - "Pendulum rope slot" on the LEFT side near the release pin
// - Placeholder release mechanism mounted along Y (pin slides in/out in Y)
// - A simple "guide window" behind the slot where the yellow-ring would pull the pendulum rope toward the gate
//
// NOTE: This is a visualization scaffold, not a mechanical CAD for tolerances.

$fn = 48;

// =====================
// Controls
// =====================
open_front = true;     // if true, remove the front (-Y) face
show_hang_lines = true;
show_inlet_lines = true;

// Box dims
box_w = 220;   // X
box_d = 120;   // Y
box_h = 120;   // Z
wall  = 10;

// Colors
wood_col  = [0.42,0.28,0.15];
brown_col = [0.45,0.25,0.10];
red_col   = [0.85,0.10,0.10];
yel_col   = [0.85,0.75,0.10];
steel_col = [0.78,0.78,0.80];

// Rope visual
rope_r = 1.1;

// Center
C = [0,0,0];

// =====================
// Vector + segment
// =====================
function vsub(a,b)=[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
function vlen(v)=sqrt(v[0]*v[0]+v[1]*v[0]*0+v[1]*v[1]+v[2]*v[2]); // keep old-safe style
function vcross(a,b)=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
function vunit(v)=let(L=vlen(v))(L<1e-9?[0,0,1]:[v[0]/L,v[1]/L,v[2]/L]);

module seg(a,b,r=1){
    v=vsub(b,a); L=vlen(v);
    if(L>1e-6){
        w=vunit(v);
        up=(abs(w[2])>0.95)?[0,1,0]:[0,0,1];
        u=vunit(vcross(up,w));
        vv=vcross(w,u);
        multmatrix([
            [u[0],vv[0],w[0],a[0]],
            [u[1],vv[1],w[1],a[1]],
            [u[2],vv[2],w[2],a[2]],
            [0,0,0,1]
        ]) cylinder(h=L,r=r);
    }
}

// =====================
// Geometry helpers
// =====================

// Top corners for brown hang
function corner(ix,iy) =
    [ C[0] + (ix?1:-1)*(box_w/2 - 14),
      C[1] + (iy?1:-1)*(box_d/2 - 14),
      C[2] + (box_h/2 - 10) ];

// Inlets (top, centered, side-by-side)
inlet_dx = 7;
function inlet_red()    = [C[0]-inlet_dx, C[1], C[2] + (box_h/2 - 14)];
function inlet_yellow() = [C[0]+inlet_dx, C[1], C[2] + (box_h/2 - 14)];

// Where ropes come from outside (placeholders)
brown_from_back  = [ -80, +70, +120 ];
brown_from_front = [ +80, -70, +120 ];
red_from_7       = [ -80, 0, +80 ];
yel_from_7       = [ +80, 0, +80 ];

// Pendulum rope slot (left wall)
slot_w = 18;   // X thickness of opening
slot_h = 10;   // Z height
slot_y = 24;   // Y thickness of opening
slot_pos = [ C[0] - (box_w/2 - wall/2), C[1] - 5, C[2] + 10 ];

// Internal "guide window" behind slot (where the pendulum rope gets pulled into the gate zone)
guide_w = 38;
guide_d = 40;
guide_h = 22;
guide_pos = [ C[0] - (box_w/2 - wall - guide_w/2 - 6), C[1], C[2] + 10 ];

// Release mechanism placeholder (mounted along Y, near the left side)
rel_body = [36, 54, 24];
rel_pos  = [ C[0] - (box_w/2 - wall - rel_body[0]/2 - 10), C[1] + 12, C[2] + 10 ];
rel_pin_r = 3.0;
rel_pin_len = 42;

// =====================
// Box shell (closed, optional open front)
// =====================
module box_shell(){
    difference(){
        // outer
        color(wood_col) translate(C) cube([box_w,box_d,box_h], center=true);

        // inner hollow
        translate(C) cube([box_w-2*wall, box_d-2*wall, box_h-2*wall], center=true);

        // optional open front (-Y face removed)
        if(open_front)
            translate([C[0], C[1] - (box_d/2 - wall/2), C[2]])
                cube([box_w+2, wall+3, box_h+3], center=true);

        // pendulum rope slot cut (through left wall)
        translate(slot_pos) cube([slot_w, slot_y, slot_h], center=true);

        // guide window cut (internal clearance zone)
        translate(guide_pos) cube([guide_w, guide_d, guide_h], center=true);

        // inlet holes (just visual holes)
        translate(inlet_red())    rotate([90,0,0]) cylinder(h=box_d+2, r=3.2, center=true);
        translate(inlet_yellow()) rotate([90,0,0]) cylinder(h=box_d+2, r=3.2, center=true);
    }
}

// Release mechanism placeholder (block + pin along Y)
module release_mech(){
    color([0.20,0.20,0.20])
        translate(rel_pos) cube(rel_body, center=true);

    color(steel_col)
        translate([rel_pos[0] + 6, rel_pos[1], rel_pos[2]])
            rotate([90,0,0]) cylinder(h=rel_pin_len, r=rel_pin_r, center=true);
}

// Ropes (visual only)
module ropes(){
    if(show_hang_lines){
        color(brown_col){
            seg(brown_from_back,  corner(0,1), rope_r);
            seg(brown_from_back,  corner(1,1), rope_r);
            seg(brown_from_front, corner(0,0), rope_r);
            seg(brown_from_front, corner(1,0), rope_r);
        }
    }
    if(show_inlet_lines){
        color(red_col) seg(red_from_7, inlet_red(), rope_r);
        color(yel_col) seg(yel_from_7, inlet_yellow(), rope_r);
    }
}

// =====================
// Scene
// =====================
box_shell();
release_mech();
ropes();

// debug: show the slot/guide volumes (comment in if you want)
// %translate(slot_pos) cube([slot_w, slot_y, slot_h], center=true);
// %translate(guide_pos) cube([guide_w, guide_d, guide_h], center=true);
