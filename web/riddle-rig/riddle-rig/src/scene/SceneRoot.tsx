import type { Dispatch } from "react";
import { useCallback, useMemo, useRef } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls, TransformControls } from "@react-three/drei";
import type { Object3D } from "three";

import type { Action } from "../core/reducer";
import type { Project } from "../core/model";
import { GridPlanes } from "./GridPlanes";
import { DiskMesh } from "./DiskMesh";
import { AnchorMesh } from "./AnchorMesh";
import { ChainMesh } from "./ChainMesh";

type Props = {
  project: Project;
  dispatch: Dispatch<Action>;
};

export function SceneRoot({ project, dispatch }: Props) {
  const orbitRef = useRef<any>(null);

  const diskObjectsRef = useRef<Record<string, Object3D | null>>({});
  const anchorObjectsRef = useRef<Record<string, Object3D | null>>({});

  const registerDiskObject = useCallback((id: string, obj: Object3D | null) => {
    diskObjectsRef.current[id] = obj;
  }, []);

  const registerAnchorObject = useCallback((id: string, obj: Object3D | null) => {
    anchorObjectsRef.current[id] = obj;
  }, []);

  const selectedObject = useMemo(() => {
    const sel = project.selection;
    if (!sel) return null;
    if (sel.type === "DISK") return diskObjectsRef.current[sel.diskId] ?? null;
    if (sel.type === "ANCHOR") return anchorObjectsRef.current[sel.anchorId] ?? null;
    return null;
  }, [
    project.selection?.type,
    project.selection && "diskId" in project.selection ? project.selection.diskId : "",
    project.selection && "anchorId" in project.selection ? project.selection.anchorId : "",
  ]);

  const tcMode = project.gizmoMode === "TRANSLATE" ? "translate" : "rotate";

  const onDraggingChanged = useCallback((e: { value: boolean }) => {
    const orbit = orbitRef.current;
    if (orbit) orbit.enabled = !e.value;
  }, []);

  const onObjectChange = useCallback(() => {
    const obj = selectedObject;
    const sel = project.selection;
    if (!obj || !sel) return;

    if (sel.type === "DISK") {
      dispatch({
        type: "SET_DISK_POSITION",
        diskId: sel.diskId,
        position: [obj.position.x, obj.position.y, obj.position.z],
      });
      dispatch({
        type: "SET_DISK_ROTATION",
        diskId: sel.diskId,
        rotation: [obj.rotation.x, obj.rotation.y, obj.rotation.z],
      });
    } else if (sel.type === "ANCHOR") {
      dispatch({
        type: "SET_ANCHOR_POSITION",
        anchorId: sel.anchorId,
        position: [obj.position.x, obj.position.y, obj.position.z],
      });
    }
  }, [
    dispatch,
    selectedObject,
    project.selection?.type,
    project.selection && "diskId" in project.selection ? project.selection.diskId : "",
    project.selection && "anchorId" in project.selection ? project.selection.anchorId : "",
  ]);

  const handlePickEndpoint = useCallback(
    (endpoint: any) => {
      if (!project.connect.enabled) return;
      dispatch({ type: "PICK_ENDPOINT", endpoint });
    },
    [dispatch, project.connect.enabled]
  );

  return (
    <Canvas
      onCreated={({ camera }) => {
        camera.up.set(0, 0, 1);
        camera.position.set(3.2, 2.6, 2.2);
        camera.lookAt(0, 0, 0.2);
        camera.updateProjectionMatrix();
      }}
      onPointerMissed={() => dispatch({ type: "CLEAR_SELECTION" })}
      camera={{ fov: 55, near: 0.01, far: 300 }}
    >
      <color attach="background" args={["#0b0f14"]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[5, 5, 8]} intensity={0.9} />

      <GridPlanes size={10} divisions={20} />

      {/* Chains */}
      {Object.values(project.chains).map((ch) => (
        <ChainMesh
          key={ch.id}
          project={project}
          chain={ch}
          selected={project.selection?.type === "CHAIN" && project.selection.chainId === ch.id}
          onSelect={() => dispatch({ type: "SET_SELECTION", selection: { type: "CHAIN", chainId: ch.id } })}
        />
      ))}

      {/* Anchors */}
      {Object.values(project.anchors).map((a) => (
        <AnchorMesh
          key={a.id}
          anchor={a}
          selected={project.selection?.type === "ANCHOR" && project.selection.anchorId === a.id}
          onSelect={() => {
            // If connect mode, also treat as endpoint pick.
            if (project.connect.enabled) {
              handlePickEndpoint({ type: "ANCHOR", anchorId: a.id });
              dispatch({ type: "SET_SELECTION", selection: { type: "ANCHOR", anchorId: a.id } });
              return;
            }
            dispatch({ type: "SET_SELECTION", selection: { type: "ANCHOR", anchorId: a.id } });
          }}
          registerObject={registerAnchorObject}
        />
      ))}

      {/* Disks */}
      {Object.values(project.disks).map((d) => (
        <DiskMesh
          key={d.id}
          disk={d}
          units={project.units}
          selected={project.selection?.type === "DISK" && project.selection.diskId === d.id}
          selectedHoleId={
            project.selection?.type === "HOLE" && project.selection.diskId === d.id
              ? project.selection.holeId
              : null
          }
          onSelectDisk={() => dispatch({ type: "SET_SELECTION", selection: { type: "DISK", diskId: d.id } })}
          onSelectHole={(holeId) => {
            if (project.connect.enabled) {
              handlePickEndpoint({ type: "HOLE", diskId: d.id, holeId });
              dispatch({ type: "SET_SELECTION", selection: { type: "HOLE", diskId: d.id, holeId } });
              return;
            }
            dispatch({ type: "SET_SELECTION", selection: { type: "HOLE", diskId: d.id, holeId } });
          }}
          registerObject={registerDiskObject}
        />
      ))}

      {/* Transform gizmo only for disk/anchor and only when we have a real Object3D */}
      {selectedObject &&
        (project.selection?.type === "DISK" || project.selection?.type === "ANCHOR") && (
          <TransformControls
            object={selectedObject}
            mode={tcMode as any}
            onDraggingChanged={onDraggingChanged as any}
            onObjectChange={onObjectChange as any}
          />
        )}

      <OrbitControls ref={orbitRef} makeDefault />
    </Canvas>
  );
}
