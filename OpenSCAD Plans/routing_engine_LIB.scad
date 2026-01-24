// routing_engine_LIB.scad

function vsub(a,b)=[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
function vlen(v)=sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);
function vcross(a,b)=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
function vunit(v)=let(L=vlen(v))(L<1e-9?[0,0,1]:[v[0]/L,v[1]/L,v[2]/L]);

module segment_between(a,b,r=1){
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

function next_node(T,p,l,i=0)=
    i>=len(T)?[-1,-1]:
    (T[i][0]==p && T[i][1]==l)?[T[i][2],T[i][3]]:
    next_node(T,p,l,i+1);

function build_path(T,p,l,max=128,s_=0)=
    s_>=max?[[p,l]]:
    let(n=next_node(T,p,l))
    (n[0]<0)?[[p,l]]:
    concat([[p,l]],build_path(T,n[0],n[1],max,s_+1));

module draw_nodes(nodes){
    for(i=[0:len(nodes)-2])
        segment_between(
            world_pt(nodes[i][0],nodes[i][1]),
            world_pt(nodes[i+1][0],nodes[i+1][1]),
            r=rope_r
        );
}
