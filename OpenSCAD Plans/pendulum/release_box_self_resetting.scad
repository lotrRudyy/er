module metal_lock_mech_one(lock_z, pulled){
  block_w = 26;
  block_h = 26;
  block_t = 24;

  bx0 = lock_x - block_w/2;
  bz0 = lock_z - 18;

  pin_y = pin_insert_y;
  if(pulled == 1) pin_y = pin_insert_y - pin_travel;
  if(pin_y < 2) pin_y = 2;

  color(metal_col){
    // guide block with Y-axis hole
    translate([bx0, 4, bz0])
      difference(){
        cube([block_w, block_t, block_h], center=false);

        // hole along Y (so we rotate cylinder)
        translate([block_w/2, -1, block_h/2])
          rotate([90,0,0])
            cylinder(h=block_t+4, r=lock_pin_r+0.25, center=false);
      }

    // lock pin (visual), axis along Y
    translate([lock_x, 4 + pin_y, lock_z - 10])
      rotate([90,0,0])
        cylinder(h=40, r=lock_pin_r, center=false);

    // outer knob in front of plate (y negative)
    translate([lock_x, -plate_t-6, lock_z - 10])
      rotate([90,0,0])
        cylinder(h=8, r=lock_pin_r+4, center=false);
  }
}
