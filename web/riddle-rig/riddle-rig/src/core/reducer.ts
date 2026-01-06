import type {
  Project,
  Selection,
  Vec3,
  DiskId,
  AnchorId,
  GizmoMode,
  ChainId,
  ChainEndpoint,
} from "./model";

export type Action =
  | { type: "NOOP" }
  | { type: "SET_SELECTION"; selection: Selection }
  | { type: "CLEAR_SELECTION" }
  | { type: "SET_GIZMO_MODE"; mode: GizmoMode }
  | { type: "SET_DISK_POSITION"; diskId: DiskId; position: Vec3 }
  | { type: "SET_DISK_ROTATION"; diskId: DiskId; rotation: Vec3 }
  | { type: "SET_ANCHOR_POSITION"; anchorId: AnchorId; position: Vec3 }
  | { type: "SET_CONNECT_ENABLED"; enabled: boolean }
  | { type: "PICK_ENDPOINT"; endpoint: ChainEndpoint }
  | { type: "DELETE_CHAIN"; chainId: ChainId }
  | { type: "SET_CHAIN_OVERRIDE"; chainId: ChainId; linkCountOverride: number | undefined }
  | { type: "RESET_PROJECT"; project: Project };

function sameEndpoint(a: ChainEndpoint, b: ChainEndpoint): boolean {
  if (a.type !== b.type) return false;
  if (a.type === "ANCHOR") return a.anchorId === (b as any).anchorId;
  return a.diskId === (b as any).diskId && a.holeId === (b as any).holeId;
}

export function reducer(project: Project, action: Action): Project {
  switch (action.type) {
    case "NOOP":
      return project;

    case "SET_SELECTION":
      return { ...project, selection: action.selection };

    case "CLEAR_SELECTION":
      return { ...project, selection: null };

    case "SET_GIZMO_MODE":
      return { ...project, gizmoMode: action.mode };

    case "SET_CONNECT_ENABLED":
      return {
        ...project,
        connect: { enabled: action.enabled, pending: action.enabled ? project.connect.pending : null },
        // while connect mode is enabled, gizmo is still allowed but selection is independent
      };

    case "PICK_ENDPOINT": {
      if (!project.connect.enabled) return project;

      const pending = project.connect.pending;
      if (!pending) {
        return { ...project, connect: { ...project.connect, pending: action.endpoint } };
      }

      // prevent self-connection
      if (sameEndpoint(pending, action.endpoint)) {
        return { ...project, connect: { ...project.connect, pending: null } };
      }

      const id = `CH${project.nextChainIndex}` as ChainId;

      return {
        ...project,
        chains: {
          ...project.chains,
          [id]: { id, a: pending, b: action.endpoint },
        },
        nextChainIndex: project.nextChainIndex + 1,
        connect: { ...project.connect, pending: null },
        selection: { type: "CHAIN", chainId: id },
      };
    }

    case "DELETE_CHAIN": {
      if (!project.chains[action.chainId]) return project;
      const next = { ...project.chains };
      delete next[action.chainId];
      const sel =
        project.selection?.type === "CHAIN" && project.selection.chainId === action.chainId
          ? null
          : project.selection;
      return { ...project, chains: next, selection: sel };
    }

    case "SET_CHAIN_OVERRIDE": {
      const ch = project.chains[action.chainId];
      if (!ch) return project;
      return {
        ...project,
        chains: {
          ...project.chains,
          [action.chainId]: { ...ch, linkCountOverride: action.linkCountOverride },
        },
      };
    }

    case "SET_DISK_POSITION": {
      const d = project.disks[action.diskId];
      if (!d) return project;
      return {
        ...project,
        disks: {
          ...project.disks,
          [action.diskId]: { ...d, transform: { ...d.transform, position: action.position } },
        },
      };
    }

    case "SET_DISK_ROTATION": {
      const d = project.disks[action.diskId];
      if (!d) return project;
      return {
        ...project,
        disks: {
          ...project.disks,
          [action.diskId]: { ...d, transform: { ...d.transform, rotation: action.rotation } },
        },
      };
    }

    case "SET_ANCHOR_POSITION": {
      const a = project.anchors[action.anchorId];
      if (!a) return project;
      return {
        ...project,
        anchors: {
          ...project.anchors,
          [action.anchorId]: { ...a, position: action.position },
        },
      };
    }

    case "RESET_PROJECT":
      return action.project;

    default: {
      const _exhaustive: never = action;
      return project;
    }
  }
}
