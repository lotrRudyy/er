// OpenSCAD 2021.01+
// 8x ANNULUS CANDLE TOKENS (2 per step/level) — scale-matched to candles.scad
// Source reference: /mnt/data/candles.scad (annulus candle icons on ring)
// Units: millimeters

$fn = 64;

// =====================
// USER PARAMETERS
// =====================
show_numbers = false;      // embossed label on body
label_is_level = true;    // if true: label = level (0..3). if false: label = copy index.

// Print-friendly tweaks
base_plate = false;        // adds a thin base under each candle for easier printing
base_plate_th = 0.60;     // mm
base_plate_margin = 0.70; // mm around the candle silhouette

// Spacing / layout
copies_per_level = 2;     // 2 per step as requested
levels = [0,1,2,3];
cell_pitch_x = 20;        // mm (center-to-center)
cell_pitch_y = 60;        // mm

// =====================
// GEOMETRY (copied from candles.scad; keep identical scale)
// =====================
mark_th        = 2.5;
ann_body_w       = 10;
ann_body_h0      = 10;
ann_body_h_step  = 10;

ann_flame_w    = 6;
ann_flame_h    = 12;
ann_flame_y_gap = 0;

font_name = "Liberation Sans:style=Bold";
ann_num_size  = 1.0;
ann_num_th    = 0.22;

// Small helper: 2D flame (as in the original)
module _flame_2d() {
  union() {
    translate([0, ann_flame_h*0.35]) circle(r=ann_flame_w*0.48);
    polygon(points=[
      [0, ann_flame_h*1.15],
      [-ann_flame_w*0.45, 0],
      [ ann_flame_w*0.45, 0]
    ]);
  }
}

// Small candle icon CENTERED at origin (original geometry)
module annulus_candle_icon_centered(level=0, label="0") {
  body_h = ann_body_h0 + level * ann_body_h_step;

  union() {
    // body
    linear_extrude(height=mark_th)
      square([ann_body_w, body_h], center=true);

    // number (embossed)
    if (show_numbers) {
      translate([0, 0, mark_th])
        linear_extrude(height=ann_num_th)
          text(label, size=ann_num_size, font=font_name, halign="center", valign="center");
    }

    // flame
    top_y = body_h/2 - ann_flame_h*0.15;
    linear_extrude(height=mark_th)
      translate([0, top_y])
        _flame_2d();
  }
}

// A printable token: candle icon + optional thin base plate
module annulus_candle_token(level=0, label="0") {
  body_h = ann_body_h0 + level * ann_body_h_step;

  // approximate bounding box for base plate
  candle_w = max(ann_body_w, ann_flame_w);
  candle_h = body_h + ann_flame_y_gap + ann_flame_h*1.15; // includes flame apex

  union() {
    if (base_plate) {
      // Slightly larger rectangle base; helps adhesion and makes parts less fragile.
      translate([0, 0, 0])
        linear_extrude(height=base_plate_th)
          square([candle_w + 2*base_plate_margin, candle_h + 2*base_plate_margin], center=true);

      // Lift the candle geometry so it sits on top of the plate
      translate([0, 0, base_plate_th])
        annulus_candle_icon_centered(level=level, label=label);
    } else {
      annulus_candle_icon_centered(level=level, label=label);
    }
  }
}

// =====================
// LAYOUT: 8 tokens (2 per level)
// =====================
module layout_8_tokens() {
  // 4 levels x 2 copies => 8 parts
  // Grid: columns = copies_per_level, rows = len(levels)
  for (r = [0:len(levels)-1]) {
    lvl = levels[r];
    for (c = [0:copies_per_level-1]) {
      idx = r*copies_per_level + c;
      lab = label_is_level ? str(lvl) : str(idx);

      translate([
        (c - (copies_per_level-1)/2) * cell_pitch_x,
        ((len(levels)-1)/2 - r) * cell_pitch_y,
        0
      ])
        annulus_candle_token(level=lvl, label=lab);
    }
  }
}

layout_8_tokens();
