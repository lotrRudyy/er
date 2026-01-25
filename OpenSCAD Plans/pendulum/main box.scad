// main box.scad
// OpenSCAD 2021.01

include <pin5_mount_structure_LIB.scad>;
include <routing_engine_LIB.scad>;
include <board_parts_LIB.scad>;
include <center_rig_LIB.scad>;

$fn = 48;

// ============================================================
// USER CONTROLS
// ============================================================
show_front_plate = false;
gap = 44;
invert_layers = true;

rope_r = 1.1;
hide_pin_outside = true;
show_fake_box = true;

// Pin knobs (wood grips) on pin3/pin7
pin_knobs_enable = true;
pin_knob_r = 18;
pin_knob_t = 14;

// ============================================================
// GLOBAL RENDER/CUTOUT FIX
// ============================================================
eps_cut = 0.2;

// ============================================================
// BOARD / GEOMETRY
// ============================================================
board_x = 1200;
board_z = 240;
plate_t = 18;

// replace every 8mm pin with 3mm pin
pin_d = 3;
pin_r = pin_d/2;

total_depth = 2*plate_t + gap;

hx = board_x/2;
hz = board_z/2;

// ============================================================
// RASTER / SLOTS
// ============================================================
margin_x = 80;
raster_h = 40;
lane_h = 10;
drop_w = pin_d + 3;
pocket_w = pin_d + 3;
pocket_h = pin_d + 3;
pocket_z_inset = 10;

raster_xL = -hx + margin_x;
raster_xR =  hx - margin_x;
raster_span = raster_xR - raster_xL;
function raster_x(i) = raster_xL + raster_span * (i/5);

raster_sep_extra = 40;

A_topZ = hz - 55 + raster_sep_extra/2;
A_botZ = A_topZ - raster_h;

C_botZ = -hz + 45 - raster_sep_extra/2;
C_topZ = C_botZ + raster_h;

// Slot removed (no cutout)
slot_len_x = 520;
slot_w_z = pin_d + 2.5;
slot_centerZ = 0;

// ============================================================
// PIN POSITIONS (X/Z) - base set
// ============================================================
pin3_i = 2;
pin7_i = 4;

pin3_x = raster_x(pin3_i);
pin7_x = raster_x(pin7_i);

X_colR = (raster_xR + hx)/2;
X_10   = (raster_xL - hx)/2;
X_8    = (X_colR + raster_xR)/2;
X_9    = -X_8;

Z_3  = A_botZ + pocket_z_inset + pocket_h/2;
Z_2  = Z_3;

Z_7  = C_botZ + pocket_z_inset + pocket_h/2;
Z_6  = Z_7;
Z_10 = Z_7;

// pin8/9: 15mm above top edge of lower raster
Z_8  = C_topZ + 15;
Z_9  = Z_8;

Z_1  = (hz + A_topZ)/2;

X_11 = pin7_x;
Y_11 = 0;
Z_11 = Z_7 - 230;

// slider X position (physical pin5)
X_5 = 150;

X_12 = X_10;

// ============================================================
// CENTER RIG INPUTS (globals consumed by center_rig_LIB.scad)
// ============================================================

// midplate geometry (the long wood board under rail parts)
midplate_y = gap;
midplate_z = 10;

// midplate length in X
midplate_x0 = X_colR - 894;
midplate_x1 = 331;

// mount spacing inset
end_inset = 21;

// colors
metal_col   = [0.80,0.80,0.82];
midwood_col = [0.50,0.34,0.20];

// guide rod stays 8mm
rod_d = 8;
rod_r = rod_d/2;
rod_y = 0;

// Place rig so TOP of pin5 mount (hanging) is 45mm above pin8/9
target_pad_botZ = Z_8 + 45;


// Must match center_rig_LIB part dims:
pb_bore_from_base_main = 20;
scs_z_main = 18;
slider_pad_z_main = 8;

midplate_centerZ =
    target_pad_botZ
  + midplate_z/2
  + pb_bore_from_base_main
  + scs_z_main/2
  + slider_pad_z_main;

// ============================================================
// REQUESTED PIN Z OVERRIDES
// - add pin15: x = pin1/pin2/... (X_colR), z = pin13z = pin14z
// - pin12z = pin15z
// - pin4z = pin5z
// ============================================================
Z_15 = pin5_anchor_world_L()[2];
Z_12 = Z_15;

pin5_z = pin5_world_Z();
Z_4 = pin5_z;  // ensure pin4z == pin5z

// ============================================================
// PIN WORLD COORDS (includes virtual 13/14 from rig lib)
// ============================================================
function P(n) =
    n==1  ? [X_colR,0,Z_1] :
    n==2  ? [X_colR,0,Z_2] :
    n==3  ? [pin3_x,0,Z_3] :
    n==4  ? [X_colR,0,Z_4] :
    n==5  ? [X_5,0,pin5_world_Z()] :
    n==6  ? [X_colR,0,Z_6] :
    n==7  ? [pin7_x,0,Z_7] :
    n==8  ? [X_8,0,Z_8] :
    n==9  ? [X_9,0,Z_9] :
    n==10 ? [X_10,0,Z_10] :
    n==11 ? [X_11,Y_11,Z_11] :
    n==12 ? [X_12,0,Z_12] :
    n==13 ? pin5_anchor_world_L() :
    n==14 ? pin5_anchor_world_R() :
    n==15 ? [X_colR,0,Z_15] :
            [0,0,0];

// ============================================================
// WASHER LAYERS
// ============================================================
washer_clear = 2;
washer_span = gap - 2*washer_clear;
washer_step = washer_span / 8;

function y_of_layer(l) =
    let(k = invert_layers ? 8-l : l)
    (-gap/2 + washer_clear + k*washer_step);

function world_pt(pin, layer) =
    let(p=P(pin)) [p[0], y_of_layer(layer), p[2]];

// ============================================================
// ROUTING TABLES
// ============================================================

// BROWN BACK (y0/y1)
brown_back_T = [
 [1,0,2,0],
 [2,0,3,0],[3,0,2,1],[2,1,4,0],
 [4,0,5,3],[5,3,4,1],[4,1,6,0],
 [6,0,7,1],[7,1,6,1],
 [6,1,8,0],[8,0,9,0],[9,0,10,0],
 [10,0,7,0]
];

// BROWN FRONT (y7/y8)
brown_front_T = [
 [1,7,2,7],
 [2,7,3,7],[3,7,2,8],[2,8,4,7],
 [4,7,5,6],[5,6,4,8],[4,8,6,7],
 [6,7,7,7],[7,7,6,8],
 [6,8,8,7],[8,7,9,7],[9,7,10,7],
 [10,7,7,8]
];

// RED (y2/y3)
red_T = [
 [1,2,2,2],
 [2,2,3,2],[3,2,2,3],[2,3,4,2],
 [4,2,5,4],[5,4,4,3],[4,3,6,2],
 [6,2,7,2],[7,2,6,3],
 [6,3,8,2],[8,2,9,2],[9,2,10,2],
 [10,2,7,3]
];

// YELLOW (y5/y6)
yellow_T = [
 [1,5,2,5],
 [2,5,3,5],[3,5,2,6],[2,6,4,5],
 [4,5,5,5],[5,5,4,6],[4,6,6,5],
 [6,5,7,5],[7,5,6,6],
 [6,6,8,5],[8,5,9,5],[9,5,10,5],
 [10,5,7,6]
];

// GREEN (y4): UPDATED (right green rope: 7 -> 6 -> 15 -> 14)
green_T = [
 [6,4,15,4],[15,4,14,4],
 [10,4,12,4],[12,4,13,4]
];

// ============================================================
// PIN3 + PIN7 WOOD KNOBS
// ============================================================
module pin_knobs_for(n){
    if(pin_knobs_enable){
        p = P(n);
        yb=+(gap/2+plate_t/2);
        yf=-(gap/2+plate_t/2);
        yb_out = yb + plate_t/2 + pin_knob_t/2 + 0.8;
        yf_out = yf - plate_t/2 - pin_knob_t/2 - 0.8;

        color([0.45,0.30,0.18]){
            translate([p[0], yb_out, p[2]]) rotate([90,0,0])
                cylinder(h=pin_knob_t, r=pin_knob_r, center=true);
            translate([p[0], yf_out, p[2]]) rotate([90,0,0])
                cylinder(h=pin_knob_t, r=pin_knob_r, center=true);
        }
    }
}

// ============================================================
// BOX (closed) + brown hang + red/yellow inlets
// ============================================================
box_w=220; box_d=120; box_h=120;
box_center=[X_11,0,Z_11-(box_h/2+40)];

function box_corner(ix,iy)=
    [box_center[0]+(ix?1:-1)*(box_w/2-14),
     box_center[1]+(iy?1:-1)*(box_d/2-14),
     box_center[2]+(box_h/2-10)];

module fake_box(){
    color([0.42,0.28,0.15])
        translate(box_center)
            cube([box_w,box_d,box_h],center=true);
}

module hang_box_brown(){
    bb=world_pt(7,0);
    bf=world_pt(7,8);
    color([0.45,0.25,0.10]){
        segment_between(bb, box_corner(0,1), r=rope_r);
        segment_between(bb, box_corner(1,1), r=rope_r);
        segment_between(bf, box_corner(0,0), r=rope_r);
        segment_between(bf, box_corner(1,0), r=rope_r);
    }
}

function box_inlet_local(dx, dz) =
    [ box_center[0] + dx,
      box_center[1],
      box_center[2] + box_h/2 - 8 + dz ];

module red_yellow_to_box(){
    p_red = world_pt(7,3);
    p_yel = world_pt(7,6);

    in_red = box_inlet_local(-10, 0);
    in_yel = box_inlet_local(+10, 0);

    color([0.85,0.10,0.10]) segment_between(p_red, in_red, r=rope_r);
    color([0.85,0.75,0.10]) segment_between(p_yel, in_yel, r=rope_r);
}

// ============================================================
// MODEL
// ============================================================
module model(){
    yb=+(gap/2+plate_t/2);
    yf=-(gap/2+plate_t/2);

    color([0.42,0.28,0.15])
    render(convexity=10)
    difference(){
        union(){
            plate(yb);
            if(show_front_plate) plate(yf);
        }
        raster_cutout(A_topZ,A_botZ);
        raster_cutout(C_topZ,C_botZ);
        pin_holes();
    }

    color([0.8,0.8,0.82]) pins_gap_only();

    pin_knobs_for(3);
    pin_knobs_for(7);

    center_assembly();

    color([0.45,0.25,0.10]){
        draw_nodes(build_path(brown_back_T,1,0));
        draw_nodes(build_path(brown_front_T,1,7));
    }
    color([0.85,0.1,0.1]) draw_nodes(build_path(red_T,1,2));
    color([0.85,0.75,0.1]) draw_nodes(build_path(yellow_T,1,5));

    color([0.1,0.75,0.15]){
        draw_nodes(concat([[7,4]],build_path(green_T,6,4)));
        draw_nodes(concat([[7,4]],build_path(green_T,10,4)));
    }

    if(show_fake_box){
        fake_box();
        hang_box_brown();
        red_yellow_to_box();
    }
}

model();
