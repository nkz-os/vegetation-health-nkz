import { useEffect } from 'react';

interface AuthInjectionMessage {
  type: 'NKZ_AUTH_INJECTION';
  token: string;
}

/**
 * Listens for NKZ_AUTH_INJECTION postMessage from nkz-mobile WebView shell.
 * Stores the Bearer token so the API client can use it as fallback when
 * the httpOnly cookie is unavailable (WebView context).
 */
export function useMobileAuth() {
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (typeof event.data === 'string') {
        try {
          const msg: AuthInjectionMessage = JSON.parse(event.data);
          if (msg.type === 'NKZ_AUTH_INJECTION' && msg.token) {
            (window as any).__nekazariMobileToken = msg.token;

            const hostAuth = (window as any).__nekazariAuthContext;
            if (!hostAuth || !hostAuth.isAuthenticated) {
              try {
                const payload = JSON.parse(atob(msg.token.split('.')[1]));
                (window as any).__nekazariAuthContext = {
                  isAuthenticated: true,
                  user: payload.preferred_username || payload.sub,
                  tenantId: payload.tenant_id || payload.tenant,
                  tenantName: payload.tenant_name || payload.tenant_id,
                  roles: payload.realm_access?.roles || [],
                };
              } catch {
                // JWT decode failed — token still available via __nekazariMobileToken
              }
            }
          }
        } catch {
          // Not JSON or not our message — ignore
        }
      }
    };

    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);
}
