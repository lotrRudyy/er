// ============================================================
// PUZZLE BOX — TRUE MECHANISM CUTS (ALIGN SOLUTION FIRST)
// - Inner cams have a CHORD CUT on the rail side (front = -Y)
// - First: solution numbers set dial angles
// - Then: cam mount offsets make flats align to the rail in that solution
// - Clearance is enforced so it won't collide while closing
// - Pointer line is BLACK
// ============================================================

$fn = 96;

// ---------------- USER CONTROLS ----------------
// You can override these, but defaults are the SOLUTION angles
// order: [circle, square, triangle]
dial_angles = [-216, -108, -180]; // circle=6, square=3, triangle=5

lid_state     = "open";   // "closed" | "open" | "removed"
lid_open_deg  = -0;      // for inspection (set 0 if you want closed)

show_outer    = true;
show_shafts   = true;
show_inner    = true;
show_numbers  = true;
show_rail     = true;
show_left_wall = false; // false = left wall removed (look inside)

// ---------------- DIMENSIONS ----------------
box_outer = [325, 200, 184];
wall      = 25;

// lid
lid_thk      = 25;
lid_overhang = 3;
lid_inset    = 0.8;

// dials
dial_xs      = [65, 162, 259];
dial_y       = 85;   // was 27 -> increased to avoid collision with rail (with clearance)

// shaft
shaft_r      = 5.0;
shaft_len_up = 10;
shaft_len_dn = 28;

// outer shapes
outer_r      = 35;
outer_t      = 10;

// POINTER LINE (black)
ptr_len      = outer_r;
ptr_w        = 1.2;
ptr_h        = 1.2;
ptr_zlift    = 0.2;

// inner cams
inner_r      = 35;
inner_t      = 10;

// chord cut depth (flat amount on rail side)
cut_depth    = 3;   // mm

// EXTRA clearance for diagonal closing motion
clearance    = 0.8;   // mm recommended

// numbers
num_radius   = 42;
num_size     = 7.0;
num_depth    = 3.2;
font_name    = "Liberation Sans:style=Bold";

// rail
rail_thk_y   = 25;    // depth (Y)
rail_drop_z  = 25;    // hang down
rail_x_pad   = 0;
rail_z_gap   = 0;

// colors
col_wood  = [0.72,0.55,0.35];
col_dark  = [0.12,0.12,0.14];
col_light = [0.82,0.82,0.85];
col_rail  = [0,0,0];

box_top_z = box_outer[2];

// hinge
hinge_y = box_outer[1] + lid_overhang - lid_inset;
hinge_z = box_top_z + lid_thk;

// ---------------- SOLUTION ALIGNMENT ----------------
// Target: in SOLUTION the flat must face the rail side (-Y).
// Our chord cut is defined on -Y in *cam local coords*.
// So we want: (dial_angle + cam_mount_deg) == 0 at solution.
// => cam_mount_deg = -solution_angle

solution_angles = [-216, -108, -180];  // circle 6, square 3, triangle 5 (0 top, clockwise)
cam_mount_deg   = [-solution_angles[0], -solution_angles[1], -solution_angles[2]];

// rail occupied Y range:
rail_y0 = wall;
rail_y1 = wall + rail_thk_y;

// required dial_y for clearance in solution:
required_dial_y = (rail_y1 + clearance) + (inner_r - cut_depth);
echo("Required dial_y >= ", required_dial_y, " ; current dial_y=", dial_y);

// ---------------- PARTS ----------------
module box_open_top(){
    color(col_wood)
    union(){
        cube([box_outer[0], box_outer[1], wall], center=false); // bottom

        // left wall (toggle)
        if(show_left_wall)
            cube([wall, box_outer[1], box_outer[2]], center=false);

        // right wall
        translate([box_outer[0]-wall, 0, 0])
            cube([wall, box_outer[1], box_outer[2]], center=false);

        // front wall
        cube([box_outer[0], wall, box_outer[2]], center=false);

        // back wall
        translate([0, box_outer[1]-wall, 0])
            cube([box_outer[0], wall, box_outer[2]], center=false);
    }
}

// rail: inside front-top, running along X, BLACK
module front_top_rail(){
    x0 = rail_x_pad;
    y0 = wall;
    z0 = box_top_z - rail_drop_z - rail_z_gap;

    color(col_rail)
    translate([x0, y0, z0])
        cube([box_outer[0] - 2*rail_x_pad, rail_thk_y, rail_drop_z], center=false);
}

module lid_geom(){
    lid_x0 = -lid_overhang + lid_inset;
    lid_y0 = -lid_overhang + lid_inset;
    lid_x1 = box_outer[0] + lid_overhang - lid_inset;
    lid_y1 = box_outer[1] + lid_overhang - lid_inset;

    difference(){
        // solid lid
        translate([lid_x0, lid_y0, box_top_z])
            cube([lid_x1-lid_x0, lid_y1-lid_y0, lid_thk], center=false);

        // engrave numbers into the wood (NOT floating)
        if(show_numbers){
            for(i=[0:2]){
                translate([dial_xs[i], dial_y, box_top_z + lid_thk - num_depth])
                    numbers_stamp();
            }
        }
    }
}


module lid_transform(){
    if(lid_state == "open"){
        translate([0, hinge_y, hinge_z])
        rotate([lid_open_deg,0,0])
        translate([0, -hinge_y, -hinge_z])
            children();
    } else if(lid_state == "closed"){
        children();
    }
}

module lid(){
    if(lid_state != "removed"){
        color(col_wood) lid_transform() lid_geom();
    }
}

module shape2d(kind){
    if(kind=="circle") circle(r=outer_r);
    else if(kind=="square") square([outer_r*1.65, outer_r*1.65], center=true);
    else polygon(points=[
        [0, outer_r],
        [-0.866*outer_r, -0.5*outer_r],
        [0.866*outer_r, -0.5*outer_r]
    ]);
}

// numbers: 0 at top, clockwise 1..9
module numbers_ring(){
    for(n=[0:9]){
        a = - (360/10) * n;
        rotate([0,0,a])
        translate([0, num_radius, 0])
        rotate([0,0,-a])
        linear_extrude(height=num_depth)
            text(str(n), size=num_size, font=font_name, halign="center", valign="center");
    }
}

// stamp used to engrave numbers into the lid (0 at top, clockwise)
module numbers_stamp(){
    for(n=[0:9]){
        a = - (360/10) * n;
        rotate([0,0,a])
        translate([0, num_radius, 0])
        rotate([0,0,-a])
        linear_extrude(height=num_depth + 0.6)
            text(str(n), size=num_size, font=font_name, halign="center", valign="center");
    }
}


// pointer groove (engraved) from center toward +Y
module pointer_groove(){
    // cut a thin slot through the whole dial thickness
    translate([-ptr_w/2, 0, -0.2])
        cube([ptr_w, ptr_len, outer_t + 0.6], center=false);
}


// INNER CAM: chord cut on rail side (-Y)
module inner_cam_chordcut_front(){
    difference(){
        cylinder(h=inner_t, r=inner_r, center=false);

        // remove "front cap" (most negative Y)
        translate([-inner_r-2, -inner_r-2, -1])
            cube([inner_r*2+4, cut_depth+2, inner_t+2], center=false);
    }
}

module dial_assembly(i){
    kind = (i==0) ? "circle" : (i==1) ? "square" : "triangle";
    angZ = dial_angles[i];

    if(lid_state != "removed"){
        lid_transform()
        translate([dial_xs[i], dial_y, box_top_z])
        union(){
            

            // rotating outer dial with engraved pointer groove (always looks black)
        if(show_outer){
            color(col_light)
            translate([0,0,lid_thk])
            rotate([0,0,angZ])
            difference(){
                linear_extrude(height=outer_t)
                    shape2d(kind);

                // engraved groove => appears black
                pointer_groove();
            }
        }


            // shaft + inner cam rotate with dial, PLUS mount offset so solution aligns flats
            rotate([0,0,angZ + cam_mount_deg[i]])
            union(){
                if(show_shafts){
                    color(col_dark)
                    translate([0,0,-shaft_len_dn])
                        cylinder(h=shaft_len_dn + lid_thk + shaft_len_up, r=shaft_r, center=false);
                }
                if(show_inner){
                    color(col_dark)
                    translate([0,0,-shaft_len_dn - inner_t])
                        inner_cam_chordcut_front();
                }
            }
        }
    }
}

// ---------------- SCENE ----------------
box_open_top();
if(show_rail) front_top_rail();
lid();
for(i=[0:2]) dial_assembly(i);
