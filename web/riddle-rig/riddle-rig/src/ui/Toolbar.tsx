import type { Dispatch } from "react";
import type { Project } from "../core/model";
import type { Action } from "../core/reducer";
import { endpointLabel } from "../core/selectors";

type Props = {
  project: Project;
  dispatch: Dispatch<Action>;
};

export function Toolbar({ project, dispatch }: Props) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <button
        className={project.gizmoMode === "TRANSLATE" ? "btn active" : "btn"}
        onClick={() => dispatch({ type: "SET_GIZMO_MODE", mode: "TRANSLATE" })}
        title="Translate (move)"
      >
        Move
      </button>
      <button
        className={project.gizmoMode === "ROTATE" ? "btn active" : "btn"}
        onClick={() => dispatch({ type: "SET_GIZMO_MODE", mode: "ROTATE" })}
        title="Rotate"
      >
        Rotate
      </button>

      <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.12)", margin: "0 4px" }} />

      <button
        className={project.connect.enabled ? "btn active" : "btn"}
        onClick={() => dispatch({ type: "SET_CONNECT_ENABLED", enabled: !project.connect.enabled })}
        title="Connect mode: click two endpoints to create a chain"
      >
        Connect
      </button>

      <button className="btn" onClick={() => dispatch({ type: "CLEAR_SELECTION" })}>
        Deselect
      </button>

      <div style={{ marginLeft: 6 }} className="mono">
        {project.connect.enabled
          ? project.connect.pending
            ? `Connect: pick B (A=${endpointLabel(project.connect.pending)})`
            : "Connect: pick A"
          : project.selection
            ? project.selection.type === "DISK"
              ? `Selected: ${project.selection.diskId}`
              : project.selection.type === "HOLE"
                ? `Selected: ${project.selection.diskId}:${project.selection.holeId}`
                : project.selection.type === "ANCHOR"
                  ? `Selected: ${project.selection.anchorId}`
                  : project.selection.type === "CHAIN"
                    ? `Selected: ${project.selection.chainId}`
                    : `Selected: ${project.selection.type}`
            : "Selected: —"}
      </div>
    </div>
  );
}
