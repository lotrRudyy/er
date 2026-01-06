import { useMemo, useReducer } from "react";
import { SceneRoot } from "../scene/SceneRoot";
import { makeDefaultProject } from "../core/model";
import { reducer } from "../core/reducer";
import { Toolbar } from "../ui/Toolbar";
import { Inspector } from "../ui/Inspector";

export function App() {
  const initial = useMemo(() => makeDefaultProject(), []);
  const [project, dispatch] = useReducer(reducer, initial);

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative" }}>
      <SceneRoot project={project} dispatch={dispatch} />
      <div className="overlay">
        <div className="toolbar panel">
          <Toolbar project={project} dispatch={dispatch} />
        </div>
        <div className="inspector panel">
          <Inspector project={project} dispatch={dispatch} />
        </div>
      </div>
    </div>
  );
}
