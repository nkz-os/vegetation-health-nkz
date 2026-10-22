import { useState, useEffect, useCallback, useMemo } from 'react';

interface AuthContext {
  token?: string;
  tenantId?: string;
  isAuthenticated: boolean;
  user?: any;
  roles?: string[];
  login?: () => void;
  logout?: () => void;
  // SDK-compatible methods
  getToken: () => string | undefined;
  getTenantId: () => string | undefined;
  hasRole: (role: string) => boolean;
}

/**
 * Custom useAuth hook that reads from the Host's window.__nekazariAuthContext.
 * Provides both direct properties (for convenience) and SDK-compatible methods.
 */
export function useAuth(): AuthContext {
  const [auth, setAuth] = useState<Omit<AuthContext, 'getToken' | 'getTenantId' | 'hasRole'>>({
    isAuthenticated: false,
    roles: []
  });

  useEffect(() => {
    const checkAuth = () => {
      const hostAuth = (window as any).__nekazariAuthContext;

      if (hostAuth) {
        const isAuth = !!hostAuth.isAuthenticated;

        if (isAuth !== auth.isAuthenticated) {
          setAuth({
            isAuthenticated: isAuth,
            tenantId: hostAuth.tenantId,
            user: hostAuth.user,
            roles: hostAuth.roles || [],
            login: hostAuth.login,
            logout: hostAuth.logout
          });
        }
      }
    };

    checkAuth();
    // Auth context is set once by the host on page load; no polling needed.
  }, []);

  // SDK-compatible methods
  const getToken = useCallback(() => auth.token, [auth.token]);
  const getTenantId = useCallback(() => auth.tenantId, [auth.tenantId]);
  const hasRole = useCallback((role: string) => auth.roles?.includes(role) ?? false, [auth.roles]);

  return useMemo(() => ({
    ...auth,
    getToken,
    getTenantId,
    hasRole
  }), [auth, getToken, getTenantId, hasRole]);
}
