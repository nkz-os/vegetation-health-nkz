/**
 * Global type declarations for Nekazari module integration
 */

declare global {
  interface Window {
    __nekazariModuleData?: {
      vegetation?: {
        sceneId?: string | null;
        indexType?: string;
        selectedDate?: string | null;
      };
      [key: string]: any; // Allow other modules to add their data
    };
  }
}

export {};









