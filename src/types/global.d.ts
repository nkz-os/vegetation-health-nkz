/**
 * Global type declarations for Nekazari module integration
 */

declare global {
  interface Window {
    __ENV__?: {
      API_URL?: string;
      VITE_API_URL?: string;
      [key: string]: unknown;
    };
    __nekazariModuleData?: {
      vegetation?: {
        sceneId?: string | null;
        indexType?: string;
        selectedDate?: string | null;
      };
      [key: string]: any; // Allow other modules to add their data
    };
    __NKZ__: {
      register: (module: any) => void;
    };
  }
}

export { };









