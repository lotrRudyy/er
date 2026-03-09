// Bridge / U-Frame — VERSTÄRKT
// Fokus: stabile Verbindungen, kein Support nötig
// alles in mm

$fn = 64;

// ---------- Parameter ----------
stand_h      = 80;
round_h      = 20;
round_r      = 7.5;

square_h     = stand_h - round_h;
square_side  = 15;

center_dist  = 64;

top_h        = 12;        // dicker als vorher
depth        = 15;

// Verstärkung
tenon_depth  = 8;         // Querbalken greift 8 mm in Ständer
tenon_width  = 10;        // Zapfenbreite
rib_thick    = 3;         // innere Rippen
rib_count    = 3;

// Abgeleitet
leg_x = center_dist/2;
top_w = center_dist + square_side;

// ---------- Module ----------
module leg(){
  difference(){
    union(){
      // rund unten
      cylinder(h = round_h, r = round_r);

      // quadratisch oben
      translate([-square_side/2, -square_side/2, round_h])
        cube([square_side, square_side, square_h]);
    }

    // Zapfenloch für Querbalken
    translate([-tenon_width/2,
               -depth/2,
               stand_h - tenon_depth])
      cube([tenon_width, depth, tenon_depth + 0.01]);
  }
}

module top_bar(){
  union(){
    // Hauptkörper
    translate([-top_w/2, -depth/2, stand_h - tenon_depth])
      cube([top_w, depth, top_h + tenon_depth]);

    // Zapfen links & rechts
    for(x = [-leg_x, leg_x])
      translate([x - tenon_width/2,
                 -depth/2,
                 stand_h - tenon_depth])
        cube([tenon_width, depth, tenon_depth]);

    // innere Rippen gegen Durchbiegen
    for(i = [1:rib_count])
      translate([
        -top_w/2 + i*(top_w/(rib_count+1)) - rib_thick/2,
        -depth/2,
        stand_h - tenon_depth
      ])
        cube([rib_thick, depth, top_h + tenon_depth]);
  }
}

module bridge(){
  union(){
    translate([-leg_x, 0, 0]) leg();
    translate([ leg_x, 0, 0]) leg();
    top_bar();
  }
}

// ---------- Ausgabe ----------
bridge();
