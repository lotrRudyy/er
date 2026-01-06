import type { Dispatch } from "react";
import type { Project, Vec3 } from "../core/model";
import type { Action } from "../core/reducer";
import { computeChainLinkCount, endpointLabel } from "../core/selectors";

type Props = {
  project: Project;
  dispatch: Dispatch<Action>;
};

function num(v: number): string {
  return Number.isFinite(v) ? String(Math.round(v * 1000) / 1000) : "0";
}

function parseOr(prev: number, next: string): number {
  const n = Number(next);
  return Number.isFinite(n) ? n : prev;
}

function Vec3Editor(props: {
  value: Vec3;
  labels: [string, string, string];
  onChange: (next: Vec3) => void;
}) {
  const { value, labels, onChange } = props;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {[0, 1, 2].map((i) => (
        <div key={i} className="field">
          <div className="label">{labels[i]}</div>
          <input
            className="input"
            value={num(value[i])}
            onChange={(e) => {
              const v0: Vec3 = [value[0], value[1], value[2]];
              v0[i] = parseOr(value[i], e.target.value);
              onChange(v0);
            }}
          />
        </div>
      ))}
    </div>
  );
}

export function Inspector({ project, dispatch }: Props) {
  const sel = project.selection;

  const chainIds = Object.keys(project.chains).sort();

  return (
    <>
      <div>
        <div className="hud-title">Inspector</div>
        <div className="hud-sub">
          {project.connect.enabled
            ? project.connect.pending
              ? `Connect mode: pick second endpoint (A=${endpointLabel(project.connect.pending)})`
              : "Connect mode: pick first endpoint"
            : "Select a disk, hole, anchor, or chain."}
        </div>
      </div>

      {sel?.type === "DISK" && (() => {
        const d = project.disks[sel.diskId];
        if (!d) return null;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="hud-title">Disk</div>
              <div className="hud-sub mono">
                {d.id} • {d.kind}
              </div>
            </div>

            <div className="row">
              <div className="label">Position (m)</div>
            </div>
            <Vec3Editor
              value={d.transform.position}
              labels={["X", "Y", "Z"]}
              onChange={(p) => dispatch({ type: "SET_DISK_POSITION", diskId: d.id, position: p })}
            />

            <div className="row" style={{ marginTop: 6 }}>
              <div className="label">Rotation (rad)</div>
            </div>
            <Vec3Editor
              value={d.transform.rotation}
              labels={["Rx", "Ry", "Rz"]}
              onChange={(r) => dispatch({ type: "SET_DISK_ROTATION", diskId: d.id, rotation: r })}
            />
          </div>
        );
      })()}

      {sel?.type === "HOLE" && (() => {
        const d = project.disks[sel.diskId];
        if (!d) return null;
        const h = d.holes[sel.holeId];
        if (!h) return null;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="hud-title">Hole</div>
              <div className="hud-sub mono">{sel.diskId}:{sel.holeId}</div>
            </div>
            <div className="hud-sub">Hole editing (fine-tuning) is next step.</div>
          </div>
        );
      })()}

      {sel?.type === "ANCHOR" && (() => {
        const a = project.anchors[sel.anchorId];
        if (!a) return null;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="hud-title">Anchor</div>
              <div className="hud-sub mono">{a.id}</div>
            </div>

            <div className="row">
              <div className="label">Position (m)</div>
            </div>
            <Vec3Editor
              value={a.position}
              labels={["X", "Y", "Z"]}
              onChange={(p) => dispatch({ type: "SET_ANCHOR_POSITION", anchorId: a.id, position: p })}
            />
          </div>
        );
      })()}

      {sel?.type === "CHAIN" && (() => {
        const ch = project.chains[sel.chainId];
        if (!ch) return null;
        const computed = computeChainLinkCount(project, ch);
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="hud-title">Chain</div>
              <div className="hud-sub mono">{ch.id}</div>
            </div>

            <div className="hud-sub mono">
              {endpointLabel(ch.a)} → {endpointLabel(ch.b)}
            </div>

            <div className="row">
              <div className="label">Link count</div>
              <div className="mono">{computed ?? "?"}</div>
            </div>

            <div className="row" style={{ gap: 8 }}>
              <div className="label">Override</div>
            </div>
            <input
              className="input mono"
              placeholder="(empty = auto)"
              value={typeof ch.linkCountOverride === "number" ? String(ch.linkCountOverride) : ""}
              onChange={(e) => {
                const raw = e.target.value.trim();
                if (raw === "") {
                  dispatch({ type: "SET_CHAIN_OVERRIDE", chainId: ch.id, linkCountOverride: undefined });
                  return;
                }
                const n = Number(raw);
                if (!Number.isFinite(n)) return;
                dispatch({ type: "SET_CHAIN_OVERRIDE", chainId: ch.id, linkCountOverride: Math.max(1, Math.round(n)) });
              }}
            />

            <button className="btn" onClick={() => dispatch({ type: "DELETE_CHAIN", chainId: ch.id })}>
              Delete chain
            </button>
          </div>
        );
      })()}

      <div style={{ height: 1, background: "rgba(255,255,255,0.10)", margin: "4px 0" }} />

      <div>
        <div className="row">
          <div className="label">Chains</div>
          <div className="mono">{chainIds.length}</div>
        </div>
        {chainIds.length === 0 ? (
          <div className="hud-sub">No chains yet. Use Connect mode.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
            {chainIds.map((id) => {
              const ch = project.chains[id as any];
              const label = ch ? `${endpointLabel(ch.a)} → ${endpointLabel(ch.b)}` : "";
              return (
                <button
                  key={id}
                  className={project.selection?.type === "CHAIN" && project.selection.chainId === id ? "btn active" : "btn"}
                  onClick={() => dispatch({ type: "SET_SELECTION", selection: { type: "CHAIN", chainId: id as any } })}
                  style={{ textAlign: "left" }}
                >
                  <span className="mono">{id}</span> <span style={{ opacity: 0.85 }}>• {label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
