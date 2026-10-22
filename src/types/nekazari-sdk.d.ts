/**
 * Type declarations for @nekazari/sdk
 * This file ensures TypeScript can resolve the SDK types correctly
 */

declare module '@nekazari/sdk' {
  export interface ViewerContextValue {
    selectedEntityId: string | null;
    selectedEntityType: string | null;
    currentDate: Date;
    isTimelinePlaying: boolean;
    activeLayers: Set<string>;
    isLayerActive: (layer: string) => boolean;
    setLayerActive: (layer: string, active: boolean) => void;
    toggleLayer: (layer: string) => void;
    isLeftPanelOpen: boolean;
    isRightPanelOpen: boolean;
    isBottomPanelOpen: boolean;
    activeContextModule: string | null;
    cesiumViewer: any;
    selectEntity: (id: string | null, type?: string | null) => void;
    clearSelection: () => void;
    setCurrentDate: (date: Date) => void;
    toggleTimelinePlayback: () => void;
    toggleLeftPanel: () => void;
    toggleRightPanel: () => void;
    toggleBottomPanel: () => void;
    setLeftPanelOpen: (open: boolean) => void;
    setRightPanelOpen: (open: boolean) => void;
    setActiveContextModule: (module: string | null) => void;
    setCesiumViewer: (viewer: any) => void;
  }

  export function useViewer(): ViewerContextValue;
  export function useViewerOptional(): ViewerContextValue | null;

  // API Client
  export interface NKZClientOptions {
    baseUrl: string;
    getToken?: () => string | undefined;
    getTenantId?: () => string | undefined;
    defaultHeaders?: Record<string, string>;
  }

  export class NKZClient {
    constructor(options: NKZClientOptions);
    request<T = unknown>(path: string, init?: RequestInit): Promise<T>;
    get<T = unknown>(path: string, init?: RequestInit): Promise<T>;
    post<T = unknown, B = unknown>(path: string, body?: B, init?: RequestInit): Promise<T>;
    put<T = unknown, B = unknown>(path: string, body?: B, init?: RequestInit): Promise<T>;
    patch<T = unknown, B = unknown>(path: string, body?: B, init?: RequestInit): Promise<T>;
    delete<T = unknown>(path: string, init?: RequestInit): Promise<T>;
  }

  // Re-export other SDK exports
  export * from '@nekazari/sdk/dist/index';
}
