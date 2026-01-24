// board_parts_LIB.scad

module raster_cutout(topZ,botZ){
    lane_top=topZ;
    lane_bot=topZ-lane_h;
    pocket_center=botZ+pocket_z_inset+pocket_h/2;
    pocket_top=pocket_center+pocket_h/2;
    drop_center=(lane_bot+pocket_top)/2;
    drop_h=max(0,lane_bot-pocket_top);

    union(){
        translate([0,0,(lane_top+lane_bot)/2])
            cube([raster_span, total_depth+2+2*eps_cut, lane_h],center=true);
        for(i=[0:5]){
            if(drop_h>0)
                translate([raster_x(i),0,drop_center])
                    cube([drop_w, total_depth+2+2*eps_cut, drop_h],center=true);
            translate([raster_x(i),0,pocket_center])
                cube([pocket_w, total_depth+2+2*eps_cut, pocket_h],center=true);
        }
    }
}

module pin_holes(){
    for(n=[1:15]) if(n!=11 && n!=5 && n!=13 && n!=14){
        p=P(n);
        translate([p[0],0,p[2]])
            rotate([90,0,0])
                cylinder(h=total_depth+4+2*eps_cut,r=pin_r+0.25,center=true);
    }
}

module plate(yc){
    translate([0,yc,0])
        cube([board_x,plate_t,board_z],center=true);
}

module pins_gap_only(){
    for(n=[1:15]) if(n!=11 && n!=5 && n!=13 && n!=14){
        p=P(n);
        translate([p[0],0,p[2]])
            rotate([90,0,0])
                cylinder(h=gap,r=pin_r,center=true);
    }
}
