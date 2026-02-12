// OpenSCAD 2021.01+
// ANNULUS RING VIEW — HOLLOW, Z-LEVELED, UNIFORM THICKNESS
//
// - Everything lies on the same Z plane
// - Uniform 1mm thickness
// - Ring walls face-aligned:
//     inner wall outer face = 70mm
//     outer wall inner face = 92mm
// - Candles are hollow + supported to outer wall
// - Radial spokes every 30°, skipping candles
// - Pointer replaced by hollow HAMMER (1mm wall) matching the reference silhouette
//   from center to 75% of inner circle radius (handle length basis)
//
// Units: millimeters

$fn = 180;

// =====================
// GLOBAL SETTINGS
// =====================
thk    = 1.0;
wall_w = 1.0;

// =====================
// VISIBILITY TOGGLES
// =====================
show_ring    = true;
show_marks   = true;
show_candles = true;
show_hammer  = true;
show_spokes  = true;

// =====================
// USER-SPECIFIED LINE SIZES
// =====================
mark_w         = 2;
outer_mark_len = 3.0;
inner_mark_len = 3.0;
ring_outline_w = 2;

// =====================
// GEOMETRY
// =====================
token_bottom_d = 184;
token_top_d    = 140;

// Candle geometry
ann_body_w      = 0.5;
ann_body_h0     = 0.55;
ann_body_h_step = 0.28;
ann_flame_w     = 0.25;
ann_flame_h     = 0.5;
ann_flame_y_gap = 0;

// Candle placement
annulus_margin      = 0.10;
candle_inner_offset = 0.12;

// Angles
candle_angles = [272, 49, 126, 329];
function candle_ang(i) = candle_angles[i];

// Hammer angle
pointer_ang_local_deg = 90;

// Wall marks
global_top_marks    = [13,80,97,210,227,60,185,170,260,290,300,340];
global_bottom_marks = [340,5,15,55,145,93,97,110,115,180,200,208,240,250,265,290];

// =====================
// HELPERS
// =====================
function ring_r_in()  = token_top_d/2;    // 70
function ring_r_out() = token_bottom_d/2; // 92
function ring_w()     = ring_r_out() - ring_r_in();

function ang_norm(a) = let(x = a % 360) (x < 0 ? x + 360 : x);
function ang_diff(a,b) = let(d = abs(ang_norm(a)-ang_norm(b))) (d > 180 ? 360-d : d);
function near_any_angle(a, arr, tol) =
    max([ for (x = arr) (ang_diff(a,x) <= tol ? 1 : 0) ]) > 0;

module extrude() {
    linear_extrude(height=thk) children();
}

module radial_tick(a, r0, len, w) {
    rotate(a)
        translate([r0, -w/2])
            square([len, w], center=false);
}

module radial_support(a, r0, r1, w) {
    rotate(a)
        translate([r0, -w/2])
            square([r1-r0, w], center=false);
}

module hollow_outline_2d(scale_factor=1.0) {
    d = wall_w / scale_factor;
    difference() {
        offset(delta=d) children();
        offset(delta=d - wall_w/scale_factor) children();
    }
}

// =====================
// CANDLE ICON (2D)
// =====================
module annulus_candle_2d(level=0) {
    body_h = ann_body_h0 + level*ann_body_h_step;

    translate([-ann_body_w/2, -body_h/2])
        square([ann_body_w, body_h]);

    translate([0, body_h/2 + ann_flame_y_gap]) {
        polygon([
            [-ann_flame_w/2, 0],
            [ ann_flame_w/2, 0],
            [0, ann_flame_h]
        ]);
        translate([0, ann_flame_h*0.18])
            circle(d=ann_flame_w);
    }
}

// =====================
// HAMMER ICON (2D)
// =====================
module hammer_2d(len) {
    let(
        handle_len = len * 1,
        head_h     = max([3, len * 0.10]),
        handle_w   = 3,

        head_total_raw = len - handle_len,
        head_total = max([12, head_total_raw]),

        head_back  = head_total * 0.65,
        head_flat  = head_total * 0.45,
        head_tip   = head_total * 0.3
    )
    union() {
        translate([-handle_w/2, 0])
            square([handle_w, handle_len], center=false);

        translate([0, handle_len - head_h])
            polygon(points=[
                [-head_back, 0],
                [ head_flat, 0],
                [ head_flat + head_tip, head_h/2],
                [ head_flat, head_h],
                [-head_back, head_h]
            ]);
    }
}

function annulus_scale_for_fit(level_max=3) =
    let(
        body_h_max = ann_body_h0 + level_max*ann_body_h_step,
        y_max = (body_h_max/2) + ann_flame_y_gap + ann_flame_h,
        y_min = -(body_h_max/2),
        span  = y_max - y_min,
        avail = ring_w() - 2*annulus_margin
    )
    avail / span;

// =====================
// MAIN
// =====================
module annulus_scene() {

    // RING WALLS
    if (show_ring)
        extrude()
            union() {
                difference() {
                    circle(r=ring_r_in());
                    circle(r=ring_r_in() - ring_outline_w);
                }
                difference() {
                    circle(r=ring_r_out() + ring_outline_w);
                    circle(r=ring_r_out());
                }
            };

    // WALL MARKS
    if (show_marks)
        extrude() {
            for (a = global_bottom_marks)
                radial_tick(a, ring_r_out(), outer_mark_len, mark_w);
            for (a = global_top_marks)
                radial_tick(a, ring_r_in() - inner_mark_len, inner_mark_len, mark_w);
        }

    // CANDLES + SUPPORTS
    if (show_candles)
        extrude() {
            s = annulus_scale_for_fit(3);

            for (p = [0:3]) {
                body_h = ann_body_h0 + p*ann_body_h_step;
                y_min  = -(body_h/2);
                y_max  = (body_h/2) + ann_flame_y_gap + ann_flame_h;

                r = ring_r_in() + candle_inner_offset - s*y_min;

                rotate(candle_ang(p))
                    translate([r, 0])
                        rotate(270)
                            scale(s)
                                hollow_outline_2d(scale_factor=s)
                                    annulus_candle_2d(p);

                if (p < 3)
                    radial_support(
                        candle_ang(p),
                        r + s*y_max,
                        ring_r_out(),
                        wall_w
                    );
            }
        }

    // SPOKES
    if (show_spokes)
        extrude()
            for (a = [0:30:330])
                if (!near_any_angle(a, candle_angles, 12))
                    radial_support(a, ring_r_in(), ring_r_out(), wall_w);

    // HAMMER
    if (show_hammer)
        extrude()
            rotate(pointer_ang_local_deg)
                rotate(-90)
                    hollow_outline_2d(scale_factor=1.0)
                        hammer_2d(0.65 * ring_r_in());

    // HAMMER SUPPORTS (EXACTLY 3)
    if (show_hammer)
        extrude() {
            hammer_tip_r = 0.75 * ring_r_in();

            // from handle tip, sideways
            radial_support(pointer_ang_local_deg + 90,
                           0,
                           ring_r_in(),
                           2);

            radial_support(pointer_ang_local_deg - 90,
                           0,
                           ring_r_in(),
                           2);

            // from hammer head straight up (global +Y)
            radial_support(90,
                           46,
                           ring_r_in(),
                           2);
        }
}

annulus_scene();
