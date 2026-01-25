// center_rig_LIB.scad
// Pillow blocks mounted to BACK (underside) of midplate -> rod BELOW plate
// Pin5 mount is MANUALLY rotated downward (simulate gravity), NO mirror logic

function _end_inset() = is_undef(end_inset) ? -10 : end_inset;

function hold_left_x()  = midplate_x0 + _end_inset();
function hold_right_x() = midplate_x1 - _end_inset();

function midplate_len_x() = abs(midplate_x1 - midplate_x0);
function midplate_cx()    = (midplate_x0 + midplate_x1)/2;
function midplate_topZ()  = midplate_centerZ + midplate_z/2;
function midplate_botZ()  = midplate_centerZ - midplate_z/2;

// ============================================================
// PURCHASED PARTS (1:1 placeholders)
// ============================================================

// Pillow block (support)
pb_base_x = 42;
pb_base_y = 32;
pb_base_z = 6;

pb_body_x = 18;
pb_body_y = 30;
pb_body_z = 33;

// rod center height measured DOWN from mounting surface (plate bottom)
pb_bore_from_base = 20;

pb_mount_d = 5.5;
pb_mount_x_off = 16;
pb_mount_y_off = 11;

pb_slit_w = 2.0;
pb_setscrew_d = 3.4;

// SCS block (slider)
scs_x = 35;
scs_y = 30;
scs_z = 18;

scs_hole_d = 3.4;
scs_hole_x_off = 12;
scs_hole_y_off = 10;

scs_bore_clear = 0.35;

// Dual SCS blocks under pad
scs_dual_enable = true;
scs_dual_spacing = 55;

// wood pad between SCS and pin5 mount = pin5 base footprint
slider_pad_x = pin5_mount_base_X();
slider_pad_y = pin5_mount_base_Y();
slider_pad_z = 8;

// rod axis height in world (below plate)
function pb_bore_z() = midplate_botZ() - pb_bore_from_base;

// pad TOP Z (below rod)
function pad_topZ() = pb_bore_z() - scs_z/2;
function pad_botZ() = pad_topZ() - slider_pad_z;

// ============================================================
// MANUAL "GRAVITY" ROTATION
// - mount is rotated 180° around X at pad_botZ
// - local [x,y,z] -> [x,-y,-z]
// ============================================================

function pin5_world_Z() = pad_botZ() - pin5_mount_rod_axis_Z();

function pin5_anchor_world_L() =
    let(a = pin5_mount_anchor_left())
    [X_5 + a[0], 0 - a[1], pad_botZ() - a[2]];

function pin5_anchor_world_R() =
    let(a = pin5_mount_anchor_right())
    [X_5 + a[0], 0 - a[1], pad_botZ() - a[2]];

// ============================================================
// GEOMETRY
// ============================================================

module midplate(){
    color(midwood_col)
        translate([midplate_cx(), 0, midplate_centerZ])
            cube([midplate_len_x(), midplate_y, midplate_z], center=true);
}

module pillow_block_at(xc){
    color(metal_col)
    difference(){
        union(){
            // base sits against UNDERSIDE of midplate
            translate([xc, 0, midplate_botZ() - pb_base_z/2])
                cube([pb_base_x, pb_base_y, pb_base_z], center=true);

            // clamp body hangs down
            translate([xc, 0, midplate_botZ() - pb_body_z/2])
                cube([pb_body_x, pb_body_y, pb_body_z], center=true);
        }

        // rod bore
        translate([xc, rod_y, pb_bore_z()])
            rotate([0,90,0])
                cylinder(h=pb_base_x+pb_body_x+6+2*eps_cut, r=rod_r+0.20, center=true);

        // slit
        translate([xc, 0, pb_bore_z() + (pb_body_z/2)*0.15])
            cube([pb_body_x+2+2*eps_cut, pb_slit_w, pb_body_z], center=true);

        // set screw (from +Y side)
        translate([xc, pb_body_y/2, pb_bore_z()])
            rotate([90,0,0])
                cylinder(h=pb_body_y+2+2*eps_cut, r=pb_setscrew_d/2, center=true);

        // mounting holes (into plate, upward)
        for(ix=[-1,1], iy=[-1,1]){
            translate([xc + ix*pb_mount_x_off, iy*pb_mount_y_off, midplate_botZ() - pb_base_z/2])
                cylinder(h=pb_base_z+2+2*eps_cut, r=pb_mount_d/2, center=true);
        }
    }
}

module iron_rod(){
    color(metal_col)
        translate([(hold_left_x()+hold_right_x())/2, rod_y, pb_bore_z()])
            rotate([0,90,0])
                cylinder(h=abs(hold_right_x()-hold_left_x()), r=rod_r, center=true);
}

module scs8uu_at(xc){
    color(metal_col)
    difference(){
        translate([xc, 0, pb_bore_z()])
            cube([scs_x, scs_y, scs_z], center=true);

        // rod bore
        translate([xc, rod_y, pb_bore_z()])
            rotate([0,90,0])
                cylinder(h=scs_x+4+2*eps_cut, r=rod_r+scs_bore_clear, center=true);

        // mounting holes (top face towards plate, purely visual)
        for(ix=[-1,1], iy=[-1,1]){
            translate([xc + ix*scs_hole_x_off, iy*scs_hole_y_off, pb_bore_z() + scs_z/2])
                cylinder(h=scs_z + 2 + 2*eps_cut, r=scs_hole_d/2, center=false);
        }
    }
}

module scs_blocks(){
    if(scs_dual_enable){
        scs8uu_at(X_5 - scs_dual_spacing/2);
        scs8uu_at(X_5 + scs_dual_spacing/2);
    } else {
        scs8uu_at(X_5);
    }
}

module wood_pad(){
    color(midwood_col)
        translate([X_5, 0, pad_topZ() - slider_pad_z/2])
            cube([slider_pad_x, slider_pad_y, slider_pad_z], center=true);
}

module pin5_mount_on_pad(){
    translate([X_5, 0, pad_botZ()])
        rotate([180,0,0])
            pin5_mount_structure(
                show_part=true,
                show_rod=false,
                part_col=[0.15,0.15,0.15],
                rod_col=metal_col
            );
}

module center_assembly(){
    midplate();
    pillow_block_at(hold_left_x());
    pillow_block_at(hold_right_x());
    iron_rod();
    scs_blocks();
    wood_pad();
    pin5_mount_on_pad();
}
